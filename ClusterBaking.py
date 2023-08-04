
from PySide2 import QtCore, QtWidgets, QtGui, QtNetwork
from PySide2.QtWidgets import *
from PySide2.QtCore import *
from PySide2.QtNetwork import QTcpSocket, QTcpServer, QHostAddress, QNetworkInterface

from vrKernelServices import vrdNode, vrdGeometryNode, vrBakeTypes, vrdTextureBakeSettings, vrdIlluminationBakeSettings

import vrScenegraph
import vrFileDialog
import vrFileIO
import vrNodePtr

import os
import uiTools
import json
import time
import csv
import datetime

# Load the .ui files. We derive widget classes from these types.
ClusterBaking_form, ClusterBaking_base = uiTools.loadUiType('ClusterBaking.ui')
ProcessWidget_form, ProcessWidget_base = uiTools.loadUiType('process.ui')
RemoteWidget_form, RemoteWidget_base = uiTools.loadUiType('remote.ui')
InfoWidget_form, InfoWidget_base = uiTools.loadUiType('info.ui')

global_clients = {}

global_port = 9999

remote_port = 9998

# 创建互斥锁
mutex = QMutex()

def getIcon(name):
    """Returns a QIcon for a button or action."""
    icon = QtGui.QIcon()
    parent_path = os.path.dirname(os.path.abspath(__file__))
    icon.addPixmap(QtGui.QPixmap(f"{parent_path}\icon\{name}_disable.svg"), QtGui.QIcon.Disabled, QtGui.QIcon.Off)
    icon.addPixmap(QtGui.QPixmap(f"{parent_path}\icon\{name}.svg"), QtGui.QIcon.Normal, QtGui.QIcon.Off)
    return icon

def get_icon_resource_path():
    parent_path = os.path.dirname(os.path.abspath(__file__))
    return f"{parent_path}\icon"


class InfoWidget(InfoWidget_form, InfoWidget_base):
    # 自定义关闭信号
    windowClosed = Signal()
    shutdownSignal = Signal()
    def __init__(self, parent, info_json_str,ip):
        super(InfoWidget, self).__init__(parent)
        self.setupUi(self)

        icon = QtGui.QIcon(os.path.join(get_icon_resource_path(), "icon.ico"))
        self.setWindowIcon(icon)

        self.info_label.setText(self.get_info_str(info_json_str))

        self.name_label.setText(ip)

        self.plainTextEdit.setReadOnly(True)

        self.scroll_bar = self.plainTextEdit.verticalScrollBar()

        self.shutdown_Button.clicked.connect(self.kill_force)

        self.setWindowTitle(f"运行日志 - {ip}")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.CustomizeWindowHint)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowMinMaxButtonsHint)
        self.resize(500, 500)

    def enable_shutdown(self, enable):
        self.shutdown_Button.setEnabled(enable)

    def kill_force(self):
        # 创建警告对话框
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Warning)
        msg_box.setText("是否强制关闭？")
        msg_box.setStandardButtons(QMessageBox.Cancel | QMessageBox.Ok)
        msg_box.setDefaultButton(QMessageBox.Cancel)

        # 处理用户的选择
        result = msg_box.exec_()
        if result == QMessageBox.Ok:
            # 用户选择了退出
            self.shutdownSignal.emit()
        else:
            # 用户选择了取消
            pass




    def input_log(self, log_text):
        self.plainTextEdit.setPlainText(log_text)
        # 滚动到底部
        self.scroll_bar.setValue(self.scroll_bar.maximum())

    def append_log(self,log_text):
        self.plainTextEdit.appendPlainText(log_text)
        # 滚动到底部
        self.scroll_bar.setValue(self.scroll_bar.maximum())

    def update_info(self,info_json_str):
        self.info_label.setText(self.get_info_str(info_json_str))


    def get_info_str(self,info_json_str):
        os_name = "null"
        cpu_name = "null"
        cpu_count = "null"
        logical_cpu_count = "null"
        mem = "null"
        gpus_list = "null"

        if info_json_str == "":
            return f"操作系统名称及版本号：{os_name}\n处理器：{cpu_name}\n物理核心数：{cpu_count}\n逻辑核心数：{logical_cpu_count}\n内存：{mem}\nGPU：{gpus_list}"

        data = json.loads(info_json_str)
        system_info = data["system"]
        os_name = system_info["os"]
        cpu_info = system_info["cpu"]
        cpu_name = cpu_info["name"]
        cpu_count = cpu_info["physic_count"]
        logical_cpu_count = cpu_info["logical_count"]
        mem = system_info["memory"]
        gpus_list = system_info["gpu"]

        gpu_info_list = []
        for i, gpu_info in enumerate(gpus_list):
            gpu_name = gpu_info["gpu_name"]
            gpu_mem = gpu_info["gpu_mem"]
            gpu_str = f"GPU {i}：\n显卡：{gpu_name}\n显存：{gpu_mem} GB"
            gpu_info_list.append(gpu_str)

        gpu_info_str = "\n".join(gpu_info_list)

        info_str = f"操作系统名称及版本号：{os_name}\n处理器：{cpu_name}\n物理核心数：{cpu_count}\n逻辑核心数：{logical_cpu_count}\n内存：{mem} GB\n{gpu_info_str}"

        return info_str



    def closeEvent(self, event):
        # 发射关闭信号
        self.windowClosed.emit()
        event.accept()

class RemoteWidget(RemoteWidget_form, RemoteWidget_base):
    deleteSignal = Signal(int)
    id = 0
    def __init__(self,parent):
        super(RemoteWidget, self).__init__(parent)
        self.setupUi(self)

        RemoteWidget.id += 1
        self.id = RemoteWidget.id

        self.parent = parent

        self.client_socket=None


        self.startButton.clicked.connect(self._onStartButtonClicked)
        self.deleteButton.clicked.connect(self._onDeleteButtonClicked)
        self.detect_Button.clicked.connect(self._onDetectButtonClicked)
        self.InfoButton.clicked.connect(self._onInfoButtonClicked)

        self.ip_lineEdit.editingFinished.connect(self.IP_change)
        self.ip_text = ""

        self.startButton.setIcon(getIcon("run"))
        self.deleteButton.setIcon(getIcon("delete"))
        self.detect_Button.setIcon(getIcon("open"))
        self.InfoButton.setIcon(getIcon("info"))

        self.status_label.hide()
        self.status_label.setText("ready")

        self.open_status = False

        self.infoWidget = None

        self.info_json_str = ""
        self.log_text = ""

    # 使用参数初始化
    def init_by_param(self, ip):
        self.ip_lineEdit.setText(ip)

    def _onInfoButtonClicked(self):
        def reject():
            self.infoWidget = None
        if not self.infoWidget:
            self.infoWidget = InfoWidget(self.parent, self.info_json_str,self.ip_lineEdit.text())
            self.infoWidget.input_log(self.log_text)
            self.infoWidget.show()
            self.infoWidget.windowClosed.connect(reject)
            self.infoWidget.shutdownSignal.connect(self.shutdown)
            self.infoWidget.enable_shutdown(self.open_status)

    def shutdown(self):
        data = {
            "type": "shutdown"
        }
        self.send_message(json.dumps(data))

    def send_message(self, str):
        delimiter = "\n"
        message = str + delimiter
        self.client_socket.write(message.encode())

    def _onStartButtonClicked(self):
        data = {
            "type": "run",
            "name": self.name_lineEdit.text(),
            "version": self.version_comboBox.currentText()
        }
        self.send_message(json.dumps(data))
        self.startButton.setEnabled(False)
        pass

    def _onDeleteButtonClicked(self):
        if self.client_socket:
            self.client_socket.close()
        self.deleteSignal.emit(self.id)


    def IP_change(self):
        if self.ip_lineEdit.text() == self.ip_text:
            return
        self.ip_text = self.ip_lineEdit.text()

        self.resetUI()


    def add_to_remote_cluster_list(self):
        if self.parent.search_row(self.ip_lineEdit.text()):
            self.parent.update_data(self.ip_lineEdit.text(), self.name_lineEdit.text(), self.version_comboBox.currentText())
        else:
            self.parent.add_data(self.ip_lineEdit.text(), self.name_lineEdit.text(), self.version_comboBox.currentText())


    def get_options_as_array(self):
        # 使用列表推导式遍历每个选项，并将其添加到新的列表中
        options_array = [self.version_comboBox.itemText(i) for i in range(self.version_comboBox.count())]
        return options_array

    def resetUI(self):
        if self.client_socket:
            self.client_socket.close()
        self.open_status = False

        if self.infoWidget:
            self.infoWidget.enable_shutdown(False)

        self.detect_Button.setIcon(getIcon("open"))
        self.detect_Button.setEnabled(True)
        self.name_lineEdit.setEnabled(False)
        self.startButton.setEnabled(False)

        self.name_lineEdit.setText("")

        self.version_comboBox.clear()
        self.version_comboBox.setEnabled(False)
        self.info_json_str = ""

    def _onDetectButtonClicked(self):
        if self.open_status:
            self.client_socket.close()

            self.resetUI()
        else:
            ip_address = self.ip_lineEdit.text()
            self.client_socket = QTcpSocket()
            self.client_socket.connectToHost(QHostAddress(ip_address), remote_port)
            self.client_socket.disconnected.connect(self.handle_disconnected)

            if self.client_socket.waitForConnected(5000):  # Wait for 5 seconds

                self.client_socket.readyRead.connect(self.read_remote_data)
                print("连接至目标主机，IP: %s" % ip_address)
            else:
                print("Failed to connect to the server.")
                self.parent._MessageBox("连接失败，目标主机IP：\n%s" % ip_address)

    def handle_disconnected(self):
        self.resetUI()
        print("断开连接")
        # self.parent._MessageBox(f"节点{self.ip_lineEdit.text()}客户端程序断开连接...")

    def read_remote_data(self):
        delimiter = "\n"
        messages = self.client_socket.readAll().data().decode().split(delimiter)
        for message in messages:
            if message:
                # print(message)
                try:
                    data = json.loads(message)
                    if data["type"] == "Info":
                        self.detect_Button.setIcon(getIcon("close"))
                        self.open_status = True

                        self.info_json_str = message

                        if self.infoWidget:
                            self.infoWidget.update_info(self.info_json_str)
                            self.infoWidget.enable_shutdown(True)

                        self.name_lineEdit.setEnabled(True)
                        self.version_comboBox.setEnabled(True)
                        self.name_lineEdit.setText(data["hostname"])
                        self.version_comboBox.clear()

                        for version in data["versions"]:
                            self.version_comboBox.addItem(version)

                        if self.parent.search_row(self.ip_lineEdit.text()):
                            version = self.parent.search_by_ip(self.ip_lineEdit.text())[2]
                            self.version_comboBox.setCurrentText(version)

                        self.startButton.setEnabled(True)

                    elif data["type"] == "error":
                        message = data["message"]

                        self.resetUI()
                        self.parent._MessageBox(message)

                    elif data["type"] == "success":
                        print("远程启动成功")
                        self.detect_Button.setEnabled(False)

                        self.ip_lineEdit.setEnabled(False)
                        self.name_lineEdit.setEnabled(False)
                        self.version_comboBox.setEnabled(False)
                        self.deleteButton.setEnabled(False)

                        self.status_label.show()
                        # 保存文件
                        self.add_to_remote_cluster_list()
                        # self.client_socket.close()
                    elif data["type"] == "output":
                        msg = data["message"]
                        message = self.truncate_string(msg, 60)
                        self.status_label.setText(message)

                        current_time = datetime.datetime.now()
                        formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")

                        log = f"{formatted_time} - {msg}"
                        log_line = log + "\n"
                        self.log_text += log_line

                        if self.infoWidget:
                            self.infoWidget.append_log(log)

                    elif data["type"] == "quit":
                        self.detect_Button.setEnabled(True)
                        self.status_label.hide()

                        self.ip_lineEdit.setEnabled(True)
                        self.name_lineEdit.setEnabled(True)
                        self.version_comboBox.setEnabled(True)
                        self.deleteButton.setEnabled(True)

                        self.resetUI()
                except:
                    pass

    def truncate_string(self, string, num):
        if len(string) > num:
            return string[:num] + "..."
        else:
            return string


class ProcessThread(QThread):
    progress_updated = Signal(int, float)
    progress_finished = Signal()
    message = Signal(bytes)
    file_write_signal = Signal(int)
    client_disconnect = Signal()

    def __init__(self, parent, id):
        super(ProcessThread, self).__init__(parent)
        self.parent = parent
        self.progress_updated.connect(parent.update_progress)
        self.message.connect(parent.read_client_data)
        self.progress_finished.connect(parent.progress_finished)
        self.client_disconnect.connect(parent.client_disconnect)
        self.file_write_signal.connect(parent.update_baking_file)
        self.id = id

    def run(self):
        global global_clients
        # 不能在构造函数__init__中初始化QTcpSocket，也不能在run()中定义为当前线程的私有变量self.sk
        # 否则报错：QObject: Cannot create children for a parent that is in a different thread.
        # 此时的QTcpSocket属于新的线程
        sk = global_clients[self.id]

        sk.readyRead.connect(lambda: self.onReadyRead(sk))
        sk.disconnected.connect(lambda: self.onDisconnected(sk))

        self.exec_()


    # 绑定读
    def onReadyRead(self, sk):
        if sk.bytesAvailable() > 0:
            # 接收消息并发射信号
            data = sk.readAll().data()
            try:
                delimiter = "\n"
                messages = data.decode().split(delimiter)
                self.message.emit(data)

            except UnicodeDecodeError:
                # Write the received data to a file

                if self.parent.baking_file.open(QIODevice.Append):
                    self.parent.baking_file.write(data)
                    # # 等待数据写入完成
                    self.parent.baking_file.close()

                self.file_write_signal.emit(len(data))


    # 绑定断开连接
    def onDisconnected(self, sk):
        print("quit")
        # 发射信号
        self.client_disconnect.emit()
        sk.close()
        self.quit()  # socket关闭时也一并结束线程

    # 发消息
    def send_message(self, message):
        sk = global_clients[self.id]

        delimiter = "\n"
        message = message + delimiter

        sk.write(message.encode())

    # 发文件
    def send_file(self,file_path):
        total_size = os.path.getsize(file_path)
        transferred_size = 0

        # 再发文件
        sk = global_clients[self.id]

        file = QFile(file_path)

        progress_start_time = time.time()

        if file.open(QIODevice.ReadOnly):
            while not file.atEnd():
                data = file.read(8192)
                sk.write(data)
                transferred_size += len(data)
                progress = (transferred_size / total_size) * 100

                remaining_time = (time.time() - progress_start_time) * (total_size - transferred_size) / transferred_size

                self.progress_updated.emit(progress, remaining_time)
            file.close()

            self.progress_finished.emit()

    def send_file_verify(self,file_path):
        total_size = os.path.getsize(file_path)

        # 先发验证数据
        end_data = {
            "type": "send_scene",
            "content": total_size
        }
        end_message = json.dumps(end_data)
        self.send_message(end_message)

        print(end_message)

class ProcessWidget(ProcessWidget_form, ProcessWidget_base):
    """
    This widget holds the UI for one entry of the process list.
    Attributes:
    """
    deleteSignal = Signal(int)
    settingSignal = Signal(int)
    id = 0
    def __init__(self, parent,client_socket):
        super(ProcessWidget, self).__init__(parent)
        self.setupUi(self)

        ProcessWidget.id += 1
        self.id = ProcessWidget.id

        self.parent = parent

        # 获取锁
        mutex.lock()

        # 在子线程中修改全局变量
        global_clients[self.id] = client_socket

        # 释放锁
        mutex.unlock()



        self.startButton.clicked.connect(self._onStartButtonClicked)
        self.deleteButton.clicked.connect(self._onDeleteButtonClicked)
        self.Settings_Btn.clicked.connect(self._onSettingsButtonClicked)
        self.getButton.clicked.connect(self._onGetButtonClicked)
        # self.process.runningStateChanged.connect(self._onRunningStateChanged)

        self.progressBar.hide()
        self.status_label_widget.hide()

        self.startButton.setIcon(getIcon("run"))
        self.deleteButton.setIcon(getIcon("delete"))
        self.getButton.setIcon(getIcon("download"))
        self.Settings_Btn.setIcon(getIcon("setting"))


        self.ProcessList = []
        self.NodeList = []
        self.NodeList_with_parent = {}

        self.currentScenePath = ""

        self.receive_transferred_size = 0
        self.receive_total_size = 0

        self.receive_baking_file_path = ""

        self.baking_file = None

        self.receive_start_time = time.time()

        self.isProcessing = False

        self.updateUI()

        self.thread = ProcessThread(self, self.id)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

        data = {
            "type": "id",
            "content": self.id
        }
        message = json.dumps(data)
        print("Message from server:", message)

        self.thread.send_message(message)

    def setHighLight(self, isHighlight):
        color = QtGui.QColor(65,65,65)  # 自定义颜色为红色
        if isHighlight:
            self.frame.setStyleSheet(f"background-color: {color.name()};")  # 设置背景颜色
        else:
            self.frame.setStyleSheet("")  # 恢复默认背景

    def client_disconnect(self):
        self._onDeleteButtonClicked()
        self.parent.try_Enabled_Delete()
        self.parent._MessageBox(f"节点{self.ClusterIP_Label.text()}断开连接...")

    # 更新进度
    def update_progress(self, progress, remaining_time):

        time_format = self.convert_seconds(remaining_time)
        self.status_label.setText(f"正在发送场景... 预计还需：{time_format}")
        self.progressBar.setValue(progress)

    def progress_finished(self):
        self.status_label.setText("正在准备烘焙...")
        self.progressBar.setValue(0)

    def update_baking_file(self,data_length):
        self.receive_transferred_size += data_length
        progress = (self.receive_transferred_size / self.receive_total_size) * 100

        remaing_time = (time.time() - self.receive_start_time) * (
                    self.receive_total_size - self.receive_transferred_size) / self.receive_transferred_size

        time_format = self.convert_seconds(remaing_time)
        self.status_label.setText(f"正在接收回传... 预计还需：{time_format}")

        self.progressBar.setValue(progress)

        if self.receive_transferred_size >= self.receive_total_size:
            print("File transfer complete")
            self.baking_file = None
            self.receive_transferred_size = 0
            self.receive_total_size = 0

            self.progressBar.hide()
            self.status_label.setText("接收完毕")

            # vrFileIO.load(self.receive_baking_file_path)

            node_ptr = vrScenegraph.createNode("Group", "importBaking", vrScenegraph.getRootNode())
            import_node = vrNodeService.getNodeFromId(node_ptr.getID())

            # vrFileIOService.importFinished.connect(lambda: self.importedFile(import_node))
            # vrFileIOService.importFinished.connect(lambda: self.importedFile(import_node))
            self.parent.import_node = import_node
            self.parent.current_import_Widget = self
            self.parent.isImportProcessing = True

            vrFileIOService.importFiles([self.receive_baking_file_path], import_node)

    # 解析客户端发来的消息
    def read_client_data(self, data):
        global global_clients
        try:
            delimiter = "\n"
            messages = data.decode().split(delimiter)

        except UnicodeDecodeError:
            pass
        else:
            for message in messages:
                # 接收消息并打印
                if message:
                    # print("Message from client:", message)

                    data = json.loads(message)

                    if data["type"] == "name":
                        name_text = global_clients[self.id].peerAddress().toString() + "-" + data["content"]
                        self.ClusterIP_Label.setText(name_text)
                    elif data["type"] == "disconnect":
                        self._onDeleteButtonClicked()
                    elif data["type"] == "baking":
                        content_data = data["content"]
                        if content_data["status"] == "start":
                            self.progressBar.setValue(0)
                        elif content_data["status"] == "progress":

                            percent = content_data["percent"]
                            state = content_data["state"]
                            remaing_time = content_data["time"]
                            time_format = self.convert_seconds(remaing_time)
                            self.status_label.setText(f"{state} 预计还需：{time_format}")
                            self.progressBar.setValue(percent)

                    elif data["type"] == "ready_to_send":
                        self.thread.send_file(self.currentScenePath)

                    elif data["type"] == "send_back_file":
                        self.receive_total_size = int(data["content"])
                        server_temp_path = self.parent.get_Temp_server_path()

                        self.receive_baking_file_path = os.path.join(server_temp_path, self.ClusterIP_Label.text() + "-BakingFile.vpb")

                        if os.path.exists(self.receive_baking_file_path):
                            # 删除文件
                            os.remove(self.receive_baking_file_path)
                            # print("原临时烘焙文件删除成功！")

                        self.progressBar.hide()
                        self.status_label.setText("烘焙完毕")
                        self.getButton.setEnabled(True)


                        # <editor-fold desc="工具函数">

    def reset(self):
        # 恢复
        self.ProcessList = []
        self.NodeList = []
        self.NodeList_with_parent = {}
        print("完成回传")
        os.remove(self.receive_baking_file_path)
        print("删除临时文件")
        self.status_label_widget.hide()

        self.deleteButton.setEnabled(True)
        self.isProcessing = False

        self.parent.try_Enabled_Delete()

        data = {
            "type": "send_back_file_finish"
        }
        self.thread.send_message(json.dumps(data))



    # 保存场景
    def save_scene(self):

        currentScenePath = vrFileIOService.getFileName()

        if len(currentScenePath) == 0:
            currentScenePath = vrFileDialog.getSaveFileName("Save As", "", ["vpb(*.vpb)"], True)
            if len(currentScenePath) == 0:
                print("user cancel the process")
                return currentScenePath

            vrFileIOService.saveFile(currentScenePath)

        currentScenePath = os.path.splitext(currentScenePath)[0] + '.vpb'

        return currentScenePath

    def convert_seconds(self,seconds):
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = (seconds % 3600) % 60

        time_format = ""
        if hours > 0:
            time_format += f"{int(hours)}小时 "
        if minutes > 0:
            time_format += f"{int(minutes)}分钟 "
        if seconds > 0 or (hours == 0 and minutes == 0):
            time_format += f"{int(seconds)}秒"

        return time_format

    # 更新UI
    def updateUI(self):
        # started = self.process.runningState == RunningState.STARTED
        # self.startButton.setEnabled(not started)
        # self.stopButton.setEnabled(started)
        # self.portEdit.setReadOnly(started)
        # runningPixmap = self.startedPixmap if started else self.stoppedPixmap
        # self.runningLabel.setPixmap(runningPixmap)
        pass

    # </editor-fold>

    # <editor-fold desc="绑定函数">
    def _onStartButtonClicked(self):
        currentScenePath = self.save_scene()
        if currentScenePath == "":
            return
        self.StartBaking_internal(currentScenePath)

    # 此函数用于批量启动
    def StartBaking_internal(self,currentScenePath):
        if len(self.ProcessList) == 0:
            return
        self.currentScenePath = currentScenePath
        self.progressBar.setValue(0)
        self.progressBar.show()
        self.status_label_widget.show()

        self.status_label.setText("正在发送场景...")

        self.parent.actionDeleteTask.setEnabled(False)

        self.startButton.setEnabled(False)
        if self.parent.current_widget_id == self.id:
            self.parent.remove_nodes_Btn.setEnabled(False)

        self.startBaking()

    def _onDeleteButtonClicked(self):
        self.deleteSignal.emit(self.id)
        pass

    def _onSettingsButtonClicked(self):
        self.settingSignal.emit(self.id)
        pass

    def _onRunningStateChanged(self, state):
        self.updateUI()

    def _onGetButtonClicked(self):
        self.status_label.setText("正在准备接收文件...")
        self.getButton.setEnabled(False)

        self.baking_file = QFile(self.receive_baking_file_path)

        data = {
            "type": "ready_to_send_baking_file"
        }
        QTimer.singleShot(500, lambda: self.thread.send_message(json.dumps(data)))

        self.progressBar.setValue(0)
        self.progressBar.show()
        self.receive_start_time = time.time()

    def startBaking(self):
        self.deleteButton.setEnabled(False)

        self.isProcessing = True

        BakingSettings_json = self.parent.getBakingSettings_JsonStr()
        self.thread.send_message(BakingSettings_json)

        processNodes_json = self.parent.getProcessNodeList_JsonStr(self.ProcessList)
        self.thread.send_message(processNodes_json)

        self.thread.send_file_verify(self.currentScenePath)

    def quitProcess(self):
        try:
            self.thread.quit()
        except:
            pass

    # </editor-fold>

class ClusterBaking(ClusterBaking_form, ClusterBaking_base):
    """
    This is the main widget for the plugin. It holds a list of processes.
    Attributes:
    """

    def __init__(self, parent=None):
        super(ClusterBaking, self).__init__(parent)
        parent.layout().addWidget(self)
        self.parent = parent
        self.setupUi(self)
        # This class derives from QMainWindow so that we can have a tool bar and a menu bar.
        # To be able to embed it into the parent widget provided by VRED we need to
        # remove the Window flag.
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.Window)
        self.processWidgets = {}
        self.lastConfigFile = ""
        self.server = None
        self.client_socket = None
        self.client_id = 0
        self.current_widget_id = 0

        self.Detail_widget.hide()

        self.groupBox_BakingSettings.hide()
        self.remote_setting_widget.hide()

        self.BakingSettings_Btn.clicked.connect(self._onOpenBakingSettings)
        self.remote_setting_Btn.clicked.connect(self._onOpenRemoteSettings)

        self.listWidget_Nodes.itemClicked.connect(self.handle_item_click)

        # signal connections
        self.actionAdd.triggered.connect(self._onCreate)
        self.actionDeleteTask.triggered.connect(self._onDeleteTask)

        self.lineEdit_HostIP.textEdited.connect(self._IP_Input)
        self.lineEdit_HostIP.editingFinished.connect(lambda: self.config_Input(False))

        host_ip = self.get_config_HostIP()
        if host_ip != "":
            self.lineEdit_HostIP.setText(host_ip)
            self.connect_Btn.setEnabled(True)

        self.ClusterName_lineEdit.editingFinished.connect(lambda: self.config_Input(False))
        self.ClusterName_lineEdit.setText(self.get_config_clusterName())

        self.connect_Btn.clicked.connect(self._onConnectToHost)
        self.disconnect_Btn.clicked.connect(self._onDisconnectToHost)

        self.add_nodes_Btn.clicked.connect(self._onAddSelectNodes)
        self.remove_nodes_Btn.clicked.connect(self._onRemoveAllNodes)

        self.add_nodes_Btn.setIcon(getIcon("create"))
        self.remove_nodes_Btn.setIcon(getIcon("clear"))


        # 传输文件用

        self.receive_total_size = 0
        self.receive_transferred_size = 0

        self.receive_file_path = ""

        self.isProcessing = False

        self.send_back_file_path = ""


        # 烘焙设置初始化
        self.render = vrBakeTypes.Renderer.GPURaytracing

        self.samples = 256

        self.indirect = 10

        self.resolution = 512

        self.render_comboBox.currentIndexChanged.connect(self.render_change)
        self.quality_comboBox.currentIndexChanged.connect(self.quality_change)
        self.samples_spinBox.valueChanged.connect(self.samples_valueChange)
        self.Indirect_spinBox.valueChanged.connect(self.Indirect_valueChange)
        self.size_spinBox.valueChanged.connect(self.resolution_valueChange)

        self.reset_bakingConfig_Btn.clicked.connect(self.reset_baking_config)

        self.read_baking_config()

        # 客户端烘焙设置
        self.client_render = vrBakeTypes.Renderer.GPURaytracing
        self.client_samples = 256
        self.client_indirect = 10
        self.client_resolution = 512

        self.light_BakeSettings = None
        self.tex_BakeSettings = None

        self.geometryNodes = []
        self.nodelist = []

        self.Baking_file_path = ""

        self.baking_start_time = time.time()
        self.isImportProcessing = False
        self.import_node = None
        self.current_import_Widget = None

        vrFileIOService.projectLoadFinished.connect(self.projectLoadFinished)
        vrFileIOService.importFinished.connect(lambda: self.importedFile())

        # 绑定函数
        vrBakeService.progressStarted.connect(self.Baking_progressStarted)
        vrBakeService.progressChanged.connect(self.Baking_progressChanged)
        vrBakeService.progressFinished.connect(self.Baking_progressFinished)


        # UI setup
        self.actionAdd.setIcon(getIcon("create"))

        self.actionDeleteTask.setIcon(getIcon("quit"))

        # 远程启动
        self.remote_run_all_Btn.setIcon(getIcon("run"))
        self.remote_delete_all_Btn.setIcon(getIcon("delete"))
        self.remote_connect_all_Btn.setIcon(getIcon("close"))
        self.remote_disconnect_all_Btn.setIcon(getIcon("open"))

        self.remote_add_Btn.clicked.connect(self._add_remote_cluster)

        self.remote_delete_all_Btn.clicked.connect(self.deleteAllRemote)

        self.remote_run_all_Btn.clicked.connect(self.startAll)

        self.remote_connect_all_Btn.clicked.connect(self.remote_connect_all)
        self.remote_disconnect_all_Btn.clicked.connect(self.remote_disconnect_all)

        self.remoteWidgets = {}



        # CSV
        self.csv_file = os.path.join(self.get_Config_path(), "RemoteList.csv")

        if not os.path.exists(self.csv_file):
            self.write_header()
        else:
            rows = self.read_data()
            for row in rows:
                print(row)
                remoteWidget = RemoteWidget(self)
                remoteWidget.deleteSignal.connect(self._onRemoteWidgetDeleted)
                self.remoteWidgets[remoteWidget.id] = remoteWidget
                self.remoteWidgetsLayout.addWidget(remoteWidget)
                remoteWidget.init_by_param(row[0])


        # 自动连接
        self.auto_connect = self.get_auto_connect()
        if self.auto_connect:
            self._onConnectToHost()

    # <editor-fold desc="远程启动">
    def remote_connect_all(self):
        for id, widget in list(self.remoteWidgets.items()):
            if not widget.open_status:
                widget._onDetectButtonClicked()

    def remote_disconnect_all(self):
        for id, widget in list(self.remoteWidgets.items()):
            if widget.open_status:
                if widget.detect_Button.isEnabled():
                    widget._onDetectButtonClicked()

    # 远程启动设置面板
    def _onOpenRemoteSettings(self):
        if self.remote_setting_widget.isHidden():
            self.remote_setting_widget.show()
        else:
            self.remote_setting_widget.hide()

    def _add_remote_cluster(self):
        remoteWidget = RemoteWidget(self)
        remoteWidget.deleteSignal.connect(self._onRemoteWidgetDeleted)
        self.remoteWidgets[remoteWidget.id] = remoteWidget
        self.remoteWidgetsLayout.addWidget(remoteWidget)

    def _onRemoteWidgetDeleted(self,id):
        remoteWidget = self.remoteWidgets.pop(id, None)

        if remoteWidget.infoWidget:
            remoteWidget.infoWidget.close()

        self.remoteWidgetsLayout.removeWidget(remoteWidget)
        self.delete_data(remoteWidget.ip_lineEdit.text())
        remoteWidget.deleteLater()

    def clearAllRemote(self):
        for id, widget in list(self.remoteWidgets.items()):
            remoteWidget = self.remoteWidgets.pop(id, None)
            remoteWidget.client_socket.close()
            self.remoteWidgetsLayout.removeWidget(remoteWidget)
            remoteWidget.deleteLater()
    def deleteAllRemote(self):
        for id, widget in list(self.remoteWidgets.items()):
            if widget.deleteButton.isEnabled():
                if widget.client_socket:
                    widget.client_socket.close()
                self._onRemoteWidgetDeleted(id)

    def startAll(self):
        for id, widget in list(self.remoteWidgets.items()):
            if widget.open_status:
                if widget.detect_Button.isEnabled():
                    widget._onStartButtonClicked()


    # </editor-fold>

    # <editor-fold desc="csv操作">
    # 写入表头
    def write_header(self):
        header = ['HostIP', 'ClusterName', 'versions']
        with open(self.csv_file, 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(header)

    # 读取数据
    def read_data(self):
        rows = []
        with open(self.csv_file, 'r') as file:
            reader = csv.reader(file)
            next(reader)
            for row in reader:
                rows.append(row)
        return rows

    # 新增数据
    def add_data(self, ip, name, version):
        with open(self.csv_file, 'a', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([ip, name, version])

    # 修改数据
    def update_data(self, ip, new_name, new_version):
        rows = []
        with open(self.csv_file, 'r') as file:
            reader = csv.reader(file)
            for row in reader:
                if row[0] == ip:
                    row[1] = new_name
                    row[2] = new_version
                rows.append(row)

        with open(self.csv_file, 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerows(rows)

    # 删除数据
    def delete_data(self, ip):
        rows = []
        with open(self.csv_file, 'r') as file:
            reader = csv.reader(file)
            for row in reader:
                if row[0] != ip:
                    rows.append(row)

        with open(self.csv_file, 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerows(rows)

    # 根据IP地址搜索数据
    def search_by_ip(self, ip):
        with open(self.csv_file, 'r') as file:
            reader = csv.reader(file)
            for row in reader:
                if row[0] == ip:
                    return row

    # 搜索行
    def search_row(self,ip):
        with open(self.csv_file, 'r') as file:
            reader = csv.reader(file)
            for row in reader:
                if row[0] == ip:
                    return True
        return False

    # </editor-fold>

    # <editor-fold desc="烘焙设置">

    def reset_baking_config(self):
        self.render = vrBakeTypes.Renderer.GPURaytracing

        self.samples = 256

        self.indirect = 10

        self.resolution = 512

        self.updateBakingConfigUI()

        self.write_baking_config()

    # 烘焙设置面板
    def _onOpenBakingSettings(self):
        if self.groupBox_BakingSettings.isHidden():
            self.groupBox_BakingSettings.show()
        else:
            self.groupBox_BakingSettings.hide()

    def render_change(self):
        i = self.render_comboBox.currentIndex()
        if i == 0:
            self.render = vrBakeTypes.Renderer.CPURaytracing
        elif i == 1:
            self.render = vrBakeTypes.Renderer.GPURaytracing

        self.write_baking_config()

    def quality_change(self):
        i = self.quality_comboBox.currentIndex()
        if i == 0:
            self.samples_spinBox.setValue(64)
        elif i == 1:
            self.samples_spinBox.setValue(128)
        elif i == 2:
            self.samples_spinBox.setValue(256)
        elif i == 3:
            self.samples_spinBox.setValue(512)
        elif i == 4:
            self.samples_spinBox.setValue(1024)
        elif i == 5:
            self.samples_spinBox.setValue(2048)

        self.write_baking_config()

    def samples_valueChange(self):
        self.samples = self.samples_spinBox.value()
        if self.samples not in [64,128,256,512,1024,2048]:
            self.quality_comboBox.setCurrentIndex(6)

    def Indirect_valueChange(self):
        self.indirect = self.Indirect_spinBox.value()
        self.write_baking_config()

    def resolution_valueChange(self):
        self.resolution = self.size_spinBox.value()
        self.write_baking_config()

    # 客户端烘焙
    def getBakingSettings(self,json_str):
        # vrBakeService.bakeToTexture()

        self.client_render, self.client_samples, self.client_indirect, self.client_resolution = self.parseBakingSettings_JsonStr(json_str)

        light_BakeSettings = vrdIlluminationBakeSettings()
        # 改动参数
        light_BakeSettings.setIndirections(self.client_indirect)

        # 默认参数
        light_BakeSettings.setDirectIlluminationMode(vrBakeTypes.DirectIlluminationMode.LightAndShadows)
        light_BakeSettings.setIndirectIllumination(True)
        light_BakeSettings.setMaterialOverride(True)
        # light_BakeSettings.setMaterialOverrideColor(QtGui.QColor(0.853, 0.853, 0.853))
        light_BakeSettings.setColorBleeding(True)


        tex_BakeSettings = vrdTextureBakeSettings()
        # 改动参数
        tex_BakeSettings.setRenderer(self.client_render)
        tex_BakeSettings.setSamples(self.client_samples)
        tex_BakeSettings.setMaximumResolution(self.client_resolution)
        tex_BakeSettings.setMinimumResolution(self.client_resolution)

        # 默认参数
        tex_BakeSettings.setShareLightmapsForClones(True)
        tex_BakeSettings.setDenoiserType(vrBakeTypes.DenoiserType.GPU)
        tex_BakeSettings.setEdgeDilation(2)
        tex_BakeSettings.setHideTransparentObjects(True)
        tex_BakeSettings.setUseExistingResolution(False)
        tex_BakeSettings.setUseDenoising(True)

        return light_BakeSettings, tex_BakeSettings

    # 获取烘焙设置Json字符串
    def getBakingSettings_JsonStr(self):
        render = "GPU"
        if self.render == vrBakeTypes.Renderer.CPURaytracing:
            render = "CPU"
        elif self.render == vrBakeTypes.Renderer.GPURaytracing:
            render = "GPU"
        data = {
            "type": "baking_setting",
            "content": {
                "render": render,
                "sample": self.samples,
                "indirect": self.indirect,
                "size": self.resolution
            }
        }
        return json.dumps(data)

    def write_baking_config(self):
        data = json.loads(self.getBakingSettings_JsonStr())
        file_path = os.path.join(self.get_Config_path(), "baking_config.json")
        self.write_json_file(data,file_path)

    def read_baking_config(self):
        file_path = os.path.join(self.get_Config_path(), "baking_config.json")
        try:
            data = self.read_json_file(file_path)
            self.render, self.samples, self.indirect, self.resolution = self.parseBakingSettings_JsonStr(json.dumps(data))

            self.updateBakingConfigUI()
        except:
            pass

    def updateBakingConfigUI(self):
        if self.render == vrBakeTypes.Renderer.CPURaytracing:
            self.render_comboBox.setCurrentIndex(0)
        elif self.render == vrBakeTypes.Renderer.GPURaytracing:
            self.render_comboBox.setCurrentIndex(1)

        self.samples_spinBox.setValue(self.samples)
        self.Indirect_spinBox.setValue(self.indirect)
        self.size_spinBox.setValue(self.resolution)

        if self.samples == 64:
            self.quality_comboBox.setCurrentIndex(0)
        elif self.samples == 128:
            self.quality_comboBox.setCurrentIndex(1)
        elif self.samples == 256:
            self.quality_comboBox.setCurrentIndex(2)
        elif self.samples == 512:
            self.quality_comboBox.setCurrentIndex(3)
        elif self.samples == 1024:
            self.quality_comboBox.setCurrentIndex(4)
        elif self.samples == 2048:
            self.quality_comboBox.setCurrentIndex(5)
        else:
            self.quality_comboBox.setCurrentIndex(6)

    # 获取节点列表Json字符串
    def getProcessNodeList_JsonStr(self, nodes_name):
        data ={
            "type": "node_list",
            "content": nodes_name
        }
        return json.dumps(data)

    # 解析json字符串并转换成节点列表
    def parseProcessNodeList_JsonStr(self, nodes_name):
        nodes = []
        vrNodeService.initFindCache()

        for node_name in nodes_name:
            node = vrNodeService.findNode(node_name)
            if node.isType(vrdGeometryNode):
                nodes.append(node)

        vrNodeService.clearFindCache()

        return nodes


    # 解析烘焙设置Json字符串
    def parseBakingSettings_JsonStr(self, json_str):
        data = json.loads(json_str)
        content_data = data["content"]
        render_str = content_data["render"]

        if render_str == "CPU":
            render = vrBakeTypes.Renderer.CPURaytracing
        elif render_str == "GPU":
            render = vrBakeTypes.Renderer.GPURaytracing

        sample = content_data["sample"]
        indirect = content_data["indirect"]
        size = content_data["size"]
        return render,sample,indirect,size

    def startBaking(self):
        # 获取节点
        self.geometryNodes = self.parseProcessNodeList_JsonStr(self.nodelist)

        # 开始烘焙
        vrBakeService.bakeToTexture(self.geometryNodes, self.light_BakeSettings, self.tex_BakeSettings)

    def Baking_progressStarted(self):
        self.baking_start_time = time.time()
        data = {
            "type": "baking",
            "content": {
                "status": "start"
            }
        }
        self.send_to_server(json.dumps(data))

    def Baking_progressChanged(self, percent, state):
        remaing_time = (time.time() - self.baking_start_time) * (100 - percent) / percent
        data = {
            "type": "baking",
            "content": {
                "status": "progress",
                "percent": percent,
                "state": state,
                "time": remaing_time
            }
        }
        self.send_to_server(json.dumps(data))

        # </editor-fold>

    def Baking_progressFinished(self):
        # data = {
        #     "type": "baking",
        #     "content": {
        #         "status": "finish",
        #     }
        # }
        # self.send_to_server(json.dumps(data))

        file_name = self.lineEdit_HostIP.text() + "-" + self.ClusterName_lineEdit.text() + "-" + "BakingNodes.vpb"
        file_path = os.path.join(self.get_Temp_cluster_path(), file_name)

        select_old_nodes = []
        for geos in self.geometryNodes:
            oldNodePtr = vrNodePtr.toNode(vrdNode(geos).getObjectId())
            select_old_nodes.append(oldNodePtr)

        vrScenegraph.selectNodes(select_old_nodes)
        if vrFileIO.saveSelectedGeometry(file_path, False):
            print("保存成功")
            self.Baking_file_path = file_path
            total_size = os.path.getsize(file_path)
            data = {
                "type": "send_back_file",
                "content": total_size
            }
            self.send_to_server(json.dumps(data))


    # 导入绑定函数
    def importedFile(self):
        if self.isImportProcessing:
            print("处理期间，导入")

            import_node = self.import_node

            old_import_node = vrNodePtr.toNode(import_node.getObjectId())

            widget = self.current_import_Widget

            for node in widget.NodeList:
                vrScenegraph.deleteNode(vrNodePtr.toNode(node.getObjectId()), True)

            nodes = []
            self.findGeosRecursive(import_node, nodes, None)
            for node in nodes:
                print(node.getName())
                geo_oldNodePtr = vrNodePtr.toNode(node.getObjectId())

                parent_Node = widget.NodeList_with_parent[node.getName()]

                from_oldNodePtr = vrNodePtr.toNode(node.getParent().getObjectId())

                to_oldNodePtr = vrNodePtr.toNode(parent_Node.getObjectId())

                vrScenegraph.moveNode(geo_oldNodePtr, from_oldNodePtr, to_oldNodePtr)

            vrScenegraph.deleteNode(old_import_node, True)
            widget.reset()
            self.isImportProcessing = False
            self.Detail_widget.hide()

            for id, widget in list(self.processWidgets.items()):
                widget.setHighLight(False)
        else:
            pass

    # </editor-fold>

    # <editor-fold desc="作为客户端">

    # IP输入框行为
    def _IP_Input(self, text):
        if len(text) != 0:
            self.connect_Btn.setEnabled(True)
        else:
            self.connect_Btn.setEnabled(False)

    def config_Input(self, reset_auto_connect):
        auto_connect = self.auto_connect
        if reset_auto_connect:
            auto_connect = False
        data={
            "ClusterName": self.ClusterName_lineEdit.text(),
            "HostIP": self.lineEdit_HostIP.text(),
            "auto_connect": auto_connect
        }
        file_path = os.path.join(self.get_Config_path(),"config.json")
        # 写入JSON文件
        self.write_json_file(data, file_path)

    def get_config_clusterName(self):
        file_path = os.path.join(self.get_Config_path(), "config.json")
        # 读取JSON文件
        try:
            data = self.read_json_file(file_path)
            return data["ClusterName"]
        except:
            return QSysInfo.machineHostName()

    def get_config_HostIP(self):
        file_path = os.path.join(self.get_Config_path(), "config.json")
        # 读取JSON文件
        try:
            data = self.read_json_file(file_path)
            return data["HostIP"]
        except:
            return ""

    def get_auto_connect(self):
        file_path = os.path.join(self.get_Config_path(), "config.json")
        # 读取JSON文件
        try:
            data = self.read_json_file(file_path)
            return data["auto_connect"]
        except:
            return False

    # 连接至服务器
    def _onConnectToHost(self):
        ip_address = self.lineEdit_HostIP.text()
        port = global_port  # Replace with the desired port number
        name = self.ClusterName_lineEdit.text()

        self.client_socket = QTcpSocket()
        self.client_socket.connectToHost(QHostAddress(ip_address), port)

        if self.client_socket.waitForConnected(5000):  # Wait for 5 seconds

            self.client_socket.readyRead.connect(lambda: self.read_server_data(self.client_socket))
            self.client_socket.disconnected.connect(self.handle_client_disconnection)

            print("Connected to the server.")

            # 创建一个 JSON 对象
            data = {
                "type": "name",
                "content": name
            }

            # 将 JSON 对象转换为字符串
            message = json.dumps(data)

            print(message)

            QTimer.singleShot(100, lambda: self.send_to_server(message))

            self.connect_Btn.setEnabled(False)
            self.disconnect_Btn.setEnabled(True)
            self.actionAdd.setEnabled(False)
            self.lineEdit_HostIP.setEnabled(False)
            self.ClusterName_lineEdit.setEnabled(False)

            self.label_ConnectStatus.setText("连接至目标主机，IP: %s" % ip_address)

            self.groupBox_Host.hide()
        else:
            print("Failed to connect to the server.")
            self._MessageBox("连接失败，目标主机IP：\n%s" % ip_address)

    # 服务器消息解析
    def read_server_data(self, client_socket):
        if client_socket.bytesAvailable() > 0:
            raw_data = client_socket.readAll()
            try:
                delimiter = "\n"
                messages = raw_data.data().decode().split(delimiter)

            except UnicodeDecodeError:

                # Write the received data to a file
                file = QFile(self.receive_file_path)

                if file.open(QIODevice.Append):
                    file.write(raw_data)
                    self.receive_transferred_size += len(raw_data)
                    file.close()

                if self.receive_transferred_size == self.receive_total_size:
                    # print("File transfer complete")

                    # 复位
                    self.receive_transferred_size = 0
                    self.receive_total_size = 0

                    vrFileIOService.newFile()
                    # vrFileIOService.projectLoadFinished.connect(self.projectLoadFinished)
                    vrFileIOService.loadFile(self.receive_file_path)
            else:
                for message in messages:
                    if message:
                        # print("Client: Message from server:", message)

                        data = json.loads(message)

                        if data["type"] == "id":
                            self.client_id = int(data["content"])
                        elif data["type"] == "send_scene":
                            self.receive_total_size = int(data["content"])
                            file_name = self.lineEdit_HostIP.text() + "-" + self.ClusterName_lineEdit.text() + "-" + "cluster.vpb"
                            self.receive_file_path = os.path.join(self.get_Temp_cluster_path(), file_name)
                            # 检查文件是否存在
                            if os.path.exists(self.receive_file_path):
                                # 删除文件
                                os.remove(self.receive_file_path)
                                # print("原接收临时文件删除成功！")

                            self.isProcessing = True
                            # print("绑定信号")



                            data = {
                                "type": "ready_to_send"
                            }
                            QTimer.singleShot(500, lambda :self.send_to_server(json.dumps(data)))

                            self.disconnect_Btn.setEnabled(False)

                        elif data["type"] == "baking_setting":
                            self.light_BakeSettings, self.tex_BakeSettings = self.getBakingSettings(message)
                        elif data["type"] == "node_list":
                            self.nodelist = data["content"]

                        elif data["type"] == "ready_to_send_baking_file":
                            self.send_file_to_server(self.Baking_file_path)

                        elif data["type"] == "send_back_file_finish":
                            currentScenePath = vrFileIOService.getFileName()
                            currentScenePath = os.path.splitext(currentScenePath)[0] + '.vpb'

                            vrFileIOService.newFile()
                            os.remove(currentScenePath)
                            # print("移除临时场景")

                            os.remove(self.send_back_file_path)
                            # print("移除临时文件")

                            self.send_back_file_path = ""


    def projectLoadFinished(self):
        if self.isProcessing:
            # print("远程控制，加载完成")

            QTimer.singleShot(500, lambda :self.startBaking())
        else:
            # print("非远程控制，正常加载")
            pass

    def send_file_to_server(self,file_path):
        self.send_back_file_path = file_path
        # 再发文件
        total_size = os.path.getsize(file_path)
        sent_data = 0

        file = QFile(file_path)

        if file.open(QIODevice.ReadOnly):
            while not file.atEnd():
                data = file.read(8192)
                self.client_socket.write(data)

                sent_data += len(data)
                progress = sent_data / total_size * 100
                # print(f"Progress:{progress}%")

            file.close()

            # print("回传文件发送完成")

            self.isProcessing = False

            self.disconnect_Btn.setEnabled(True)

    # 与服务器断开连接时调用
    def handle_client_disconnection(self):
        self.connect_Btn.setEnabled(True)
        self.disconnect_Btn.setEnabled(False)
        self.actionAdd.setEnabled(True)
        self.lineEdit_HostIP.setEnabled(True)
        self.ClusterName_lineEdit.setEnabled(True)

        self.label_ConnectStatus.setText("当前没有任何连接")

        self.groupBox_Host.show()

        self.config_Input(True)
        # 指定进程名
        process_name = 'VREDPro.exe'

        # 使用taskkill命令杀死进程
        os.system(f'taskkill /F /IM {process_name}')

    # 手动断开与服务器连接
    def _onDisconnectToHost(self):
        data = {
            "type": "disconnect",
            "content": self.client_id
        }

        message = json.dumps(data)

        self.send_to_server(message)

    # </editor-fold>

    # <editor-fold desc="作为服务器">

    # 创建服务器
    def _onCreate(self):

        def get_local_ip():
            interfaces = QNetworkInterface.allInterfaces()

            for interface in interfaces:
                if interface.flags() & QNetworkInterface.IsUp and not interface.flags() & QNetworkInterface.IsLoopBack:
                    addresses = interface.addressEntries()

                    for address in addresses:
                        if address.ip().toString() != "127.0.0.1" and address.ip().toString().count(":") == 0:
                            return address.ip().toString()

        ip_address = get_local_ip()  # 替换为服务器的IP地址
        port = global_port  # 替换为所需的端口号

        self.server = QTcpServer()
        self.server.listen(QHostAddress(ip_address), port)

        if self.server.isListening():
            print("Server started. Waiting for incoming connections...")
            self.actionAdd.setEnabled(False)
            self.actionDeleteTask.setEnabled(True)
            self.lineEdit_TaskStatus.setText("正在作为服务主机...")
            self.groupBox_Client.hide()

            self.BakingSettings_Btn.setEnabled(True)
            self.remote_setting_Btn.setEnabled(True)
        else:
            print("Failed to start the server.")
            self._MessageBox("Failed to start the server.")

        self.server.newConnection.connect(self.handle_new_connection)

    # 当客户端连接进来时调用
    def handle_new_connection(self):

        client_socket = self.server.nextPendingConnection()

        self.addProcess(client_socket)

    # 添加线程
    def addProcess(self, client_socket):

        # create widget for process
        procWidget = ProcessWidget(self, client_socket)
        procWidget.deleteSignal.connect(self._onProcessWidgetDeleted)
        procWidget.settingSignal.connect(self._onProcessWidgetSettings)
        self.processWidgets[procWidget.id] = procWidget
        self.processWidgetsLayout.addWidget(procWidget)

    # 删除服务器
    def _onDeleteTask(self):
        # Ask for confirmation before deleting everything.
        msgTitle = "联机烘焙"
        msgText = "停止并退出联机烘焙？\n不可撤销"
        msgBox = QMessageBox(QMessageBox.Warning, msgTitle, msgText, QMessageBox.NoButton, self)
        deleteButton = msgBox.addButton("退出", QMessageBox.ActionRole)
        cancelButton = msgBox.addButton(QMessageBox.Cancel)
        msgBox.setWindowIcon(QtGui.QIcon(os.path.join(get_icon_resource_path(), "icon.ico")))
        msgBox.exec_()
        if msgBox.clickedButton() == deleteButton:
            self.deleteAllProcesses()

    # 删除UI
    def _onProcessWidgetDeleted(self, id):
        procWidget = self.processWidgets.pop(id, None)
        self._deleteWidget(procWidget,id)

    # 删除UI
    def _deleteWidget(self, procWidget,id):
        global global_clients
        if procWidget is not None:
            # 获取锁
            mutex.lock()

            # 在子线程中修改全局变量
            client = global_clients.pop(id, None)

            # 释放锁
            mutex.unlock()

            client.close()

            self.processWidgetsLayout.removeWidget(procWidget)
            self.Detail_widget.hide()
            procWidget.quitProcess()
            procWidget.deleteLater()

    # 删除所有的任务
    def deleteAllProcesses(self):
        for id, widget in list(self.processWidgets.items()):
            self._deleteWidget(widget,id)
        self.processWidgets = {}

        if self.server:
            self.server.close()
            print("Server close.")

        self.actionAdd.setEnabled(True)
        self.actionDeleteTask.setEnabled(False)
        self.lineEdit_TaskStatus.setText("当前没有创建任何任务")
        self.groupBox_Client.show()

        self.BakingSettings_Btn.setEnabled(False)
        self.remote_setting_Btn.setEnabled(False)

        self.groupBox_BakingSettings.hide()
        self.remote_setting_widget.hide()

    def try_Enabled_Delete(self):
        processing_widgets = []
        for id, widget in list(self.processWidgets.items()):
            if widget.isProcessing == True:
                processing_widgets.append(widget)
        if len(processing_widgets) == 0:
            self.actionDeleteTask.setEnabled(True)

    def get_currentScenePath(self):
        currentScenePath = vrFileIOService.getFileName()

        if len(currentScenePath) == 0:
            currentScenePath = vrFileDialog.getSaveFileName("Save As", "", ["vpb(*.vpb)"], True)
            if len(currentScenePath) == 0:
                print("user cancel the process")
                return currentScenePath

            vrFileIOService.saveFile(currentScenePath)

        currentScenePath = os.path.splitext(currentScenePath)[0] + '.vpb'

        return currentScenePath

    # </editor-fold>

    # <editor-fold desc="节点面板">

    # 多线程UI的细节设置按钮被触发时
    def _onProcessWidgetSettings(self, id):
        self.current_widget_id = id
        self.Detail_widget.show()
        proc_widget = self.processWidgets[self.current_widget_id]

        for id, widget in list(self.processWidgets.items()):
            widget.setHighLight(False)
        proc_widget.setHighLight(True)


        self.listwidget_label.setText(f"{proc_widget.ClusterIP_Label.text()}\n待处理节点列表:")

        self.listWidget_Nodes.clear()
        nodes_name = proc_widget.ProcessList
        for node_name in nodes_name:
            icon = QtGui.QIcon(os.path.join(get_icon_resource_path(), "list_icon.svg"))
            self.listWidget_Nodes.addItem(QListWidgetItem(icon, node_name))

        self.handle_Add_remove_node_UI(len(nodes_name) > 0)



    # 添加节点
    def _onAddSelectNodes(self):
        # nodes = vrScenegraphService.getSelectedSubtreeNodes()
        nodes = vrNodeService.getSelectedNodes()
        if len(nodes) == 0:
            return
        procWidget = self.processWidgets[self.current_widget_id]
        for node in nodes:
            geos = []
            self.findGeosRecursive(node, geos, None)
            for geo in geos:
                geo_name = geo.getName()

                icon = QtGui.QIcon(os.path.join(get_icon_resource_path(), "list_icon.svg"))
                self.listWidget_Nodes.addItem(QListWidgetItem(icon, geo_name))

                procWidget.ProcessList.append(geo_name)

                procWidget.NodeList.append(geo)

                procWidget.NodeList_with_parent[geo_name] = geo.getParent()

        self.handle_Add_remove_node_UI(True)

    # 移除所有节点
    def _onRemoveAllNodes(self):
        procWidget = self.processWidgets[self.current_widget_id]
        procWidget.ProcessList = []
        procWidget.NodeList = []
        procWidget.NodeList_with_parent = {}

        self.listWidget_Nodes.clear()

        self.handle_Add_remove_node_UI(False)

    # 节点面板UI更新
    def handle_Add_remove_node_UI(self, IsAdd):
        self.add_nodes_Btn.setEnabled(not IsAdd)
        self.remove_nodes_Btn.setEnabled(IsAdd)

        procWidget = self.processWidgets[self.current_widget_id]

        procWidget.startButton.setEnabled(len(procWidget.ProcessList) != 0)

        if procWidget.isProcessing:
            self.remove_nodes_Btn.setEnabled(False)
            procWidget.startButton.setEnabled(False)



    # 节点列表点击
    def handle_item_click(self, item):
        node_name = item.text()
        vrScenegraph.selectNode(node_name)

    # </editor-fold>

    # <editor-fold desc="工具函数">

    # 读取JSON文件
    def read_json_file(self,file_path):
        with open(file_path, 'r') as file:
            data = json.load(file)
        return data

    # 写入JSON文件
    def write_json_file(self,data, file_path):
        with open(file_path, 'w') as file:
            json.dump(data, file, indent=4)

    # 消息框
    def _MessageBox(self, message):
        msgBox = QtWidgets.QMessageBox()
        msgBox.setWindowTitle("联机烘焙")
        msgBox.setInformativeText(message)
        msgBox.setStandardButtons(QtWidgets.QMessageBox.Ok)
        msgBox.exec_()

    # 递归获取网格体
    def findGeosRecursive(self, node, geos, predicate):
        """ Recursively traverses the scenegraph starting at node
            and collects geometry nodes which can be filtered
            with a predicate.
           参数：
                node (vrdNode): Currently traversed node
                geos (list of vrdGeometryNode): List of collected geometry nodes
                predicate (function): None or predicate(vrdGeometryNode)->bool
        """
        geo = vrdGeometryNode(node)
        if geo.isValid():
            if predicate is None or predicate(geo):
                geos.append(geo)
            # stop traversing the tree
        else:
            # traverse the children
            for child in node.getChildren():
                self.findGeosRecursive(child, geos, predicate)

    # 获取临时文件夹路径
    def get_Temp_folder_path(self):
        documents_path = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        path = os.path.join(documents_path, "ClusterBaking")
        # 创建目录
        directory = QDir()
        directory.mkdir(path)

        return path

    # 获取临时文件夹路径
    def get_Temp_server_path(self):
        path = os.path.join(self.get_Temp_folder_path(), "Server")
        # 创建目录
        directory = QDir()
        directory.mkdir(path)

        return path

    # 获取临时文件夹路径
    def get_Temp_cluster_path(self):
        path = os.path.join(self.get_Temp_folder_path(), "Cluster")
        # 创建目录
        directory = QDir()
        directory.mkdir(path)

        return path

    # 获取临时文件夹路径
    def get_Config_path(self):
        path = os.path.join(self.get_Temp_folder_path(), "Config")
        # 创建目录
        directory = QDir()
        directory.mkdir(path)

        return path

    def send_to_server(self, message):
        delimiter = "\n"
        message = message + delimiter
        self.client_socket.write(message.encode())

    # </editor-fold>


def onDestroyVREDScriptPlugin():
    """
    onDestroyVREDScriptPlugin() is called before this plugin is destroyed.
    In this plugin we want to stop all processes.
    """
    ClusterBakingPlugin.config_Input(True)
    ClusterBakingPlugin.clearAllRemote()
    ClusterBakingPlugin.deleteAllProcesses()

# Create the plugin widget
ClusterBakingPlugin = ClusterBaking(VREDPluginWidget)