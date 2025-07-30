from sys import argv
from PyQt6.QtWidgets import QMainWindow, QApplication
from PyQt6.uic import loadUi
from pyqtgraph import mkPen
from PyQt6 import QtGui
import random
import asyncio
from PyQt6.QtCore import QTimer, pyqtSignal, QObject

from threading import Thread, Lock
from time import sleep, perf_counter
import pyvisa

# Инициализация менеджера ресурсов VISA для общения с измерительными приборами
visa_manager = pyvisa.ResourceManager()
resource_lock = Lock()  # Мьютекс для безопасного доступа к устройствам из разных потоков

device_connected = False
should_stop = 0  # Флаг для остановки измерений из другого потока

# Глобальный словарь, содержащий текущие значения времени, сопротивления и температуры
sensor_data = {
    'time': None,
    'resistance': None,
    'temperature': None,
    'res_active': 0,   # Флаг успешного чтения сопротивления
    'temp_active': 0   # Флаг успешного чтения температуры
}

# Уникальные строки-идентификаторы устройств
device_identifiers = {
    'multimeter_a': 'KEITHLEY INSTRUMENTS INC.,MODEL 2110,1420226,01.02-00-00\n',
    'multimeter_b': 'KEITHLEY INSTRUMENTS INC.,MODEL 2110,1420239,01.02-00-00\n',
    'temperature_sensor': 'Hello - Pt Sensor\r\n'
}

# Таблица подключенных устройств (пока None)
connected_devices = {
    device_identifiers['multimeter_a']: None,
    device_identifiers['multimeter_b']: None,
    device_identifiers['temperature_sensor']: None
}

# Функция для подключения к доступным устройствам
# Ищет известные идентификаторы и записывает найденные устройства в connected_devices
# Использует query для определения устройства
# mode - если передан, включает печать отладочной информации

def connect_instruments(devices, display_errors=0, mode=None):
    available_resources = visa_manager.list_resources()
    for resource_id in available_resources:
        try:
            inst = visa_manager.open_resource(resource_id)
            inst.timeout = 1000
            inst.baud_rate = 9600
            inst.data_bits = 8
            inst.stop_bits = pyvisa.constants.StopBits(20)
            inst.parity = pyvisa.constants.Parity(4)
            _ = query_device('H\r', inst, mode)
            idn_response = query_device('H\r', inst, mode)
            is_temp_sensor = True
            if not idn_response:
                idn_response = query_device('*IDN?', inst, mode)
                is_temp_sensor = False
            if idn_response and idn_response in devices:
                devices[idn_response] = [inst, is_temp_sensor]
        except Exception as e:
            if display_errors:
                print(e)
    return display_connected_devices(device_identifiers, mode)

# Функция для отображения подключённых устройств

def display_connected_devices(device_ids, mode=None):
    output = ""
    global device_connected, current_multimeter
    if connected_devices[device_ids['multimeter_a']]:
        current_multimeter = 'multimeter_a'
    elif connected_devices[device_ids['multimeter_b']]:
        current_multimeter = 'multimeter_b'

    if mode:
        print('Devices: ')
    output += 'Devices: \n'
    for key in device_ids:
        dev = connected_devices[device_ids[key]]
        if dev:
            if mode:
                print(f"{key} connected on {dev[0].resource_name}")
            output += f"{key} connected on {dev[0].resource_name}\n"
        else:
            if mode:
                print(f"{key} didn’t connect")
            output += f"{key} didn’t connect\n"
    return output

# Обёртки для операций чтения/записи к приборам

def read_response(command, inst, display_errors=0):
    try:
        return inst.read(command)
    except Exception as e:
        if display_errors:
            print('Read error')
        return None

def send_command(command, inst):
    return inst.write(command)

# Выполняет запрос (query) к устройству. Использует мьютекс lock,
# чтобы избежать одновременного доступа из разных потоков (thread-safe)

def query_device(command, inst, display_errors=0, mode=None):
    with resource_lock:
        try:
            return inst.query(command)
        except Exception as e:
            if display_errors:
                print('Query error:', e)
            return None

# Запрашивает измерение от устройства (в зависимости от его типа)

def fetch_sensor_reading(device_info):
    return query_device('READ?', device_info[0])

# Получение и обновление данных из заданного устройства и типа измерения

def update_sensor_data(device_key, measurement_type, display_errors=0):
    try:
        global sensor_data, current_multimeter
        if not device_key:
            device_key = input('Enter device name: ')
        if device_identifiers.get(device_key) and connected_devices[device_identifiers[device_key]]:
            if measurement_type == 'resistance':
                val = float(fetch_sensor_reading(connected_devices[device_identifiers[device_key]]))
                if val:
                    sensor_data['resistance'] = val
                    sensor_data['res_active'] = 1
                else:
                    sensor_data['res_active'] = 0
            else:
                temp_val = float(fetch_sensor_reading(connected_devices[device_identifiers[device_key]])[4:-1]) * 0.01
                if temp_val:
                    sensor_data['temperature'] = temp_val
                    sensor_data['temp_active'] = 1
        else:
            print('Device not connected')
    except Exception as e:
        if display_errors:
            print(e)

# Основной цикл получения данных с датчиков. Вызывается из отдельного потока.
# Проверяет, активны ли оба датчика, и обновляет словарь sensor_data

time_flag = 1
initial_time = 0

def sample_data(interval=1, verbose=0):
    global sensor_data, current_multimeter, should_stop, time_flag, initial_time
    if time_flag:
        initial_time = perf_counter()
        time_flag = 0
    sensor_data['time'] = perf_counter() - initial_time
    sensor_data['res_active'] = 0
    sensor_data['temp_active'] = 0
    update_sensor_data(current_multimeter, 'resistance')
    update_sensor_data('temperature_sensor', 'temperature')
    if not (sensor_data['res_active'] and sensor_data['temp_active']):
        should_stop = 1
    if verbose:
        print(sensor_data)

# Для отладки — генерация фейковых данных

counter = 0
start_timestamp = perf_counter()

def generate_dummy_data():
    global sensor_data, should_stop, counter, start_timestamp
    sensor_data['time'] = perf_counter() - start_timestamp
    sensor_data['temp_active'] = counter
    sensor_data['res_active'] = counter
    sleep(1)
    counter += 1
    print(counter)
    if counter == 10:
        should_stop = 1
        counter = 0

# Сигнал обновления для интерфейса
class UpdateNotifier(QObject):
    signal_update = pyqtSignal()

# Основное окно приложения
class DataAcquisitionApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.notifier = UpdateNotifier()
        self.notifier.signal_update.connect(self.refresh_console)
        self.ui = loadUi('C:/Users/user/Documents/ryt/qtdes.ui', self)

        # Назначение действий на кнопки
        self.ui.btnConnect.clicked.connect(self.connect_devices)
        self.ui.btnStart.clicked.connect(self.start_measurement)
        self.ui.btnStop.clicked.connect(self.stop_measurement)
        self.ui.btnGraphTemp.clicked.connect(self.set_temp_graph)
        self.ui.btnGraphResist.clicked.connect(self.set_res_graph)
        self.ui.btnGraphTempResist.clicked.connect(self.set_combined_graph)

        # Настройка графика
        self.ui.graph.setBackground('w')
        self.temp_data = []
        self.res_data = []
        self.time_data = []
        self.graph_mode = 1
        self.res_range = [0, 0]
        self.temp_range = [0, 0]
        self.enable_plotting = 1
        self.console_message = None
        self.loop_index = 1

        with open('data.txt', 'w') as f:
            f.write('Time, Resistance, Temperature\n')

    # Потоковая функция, собирающая данные и рисующая график
    def collect_data(self):
        while not should_stop:
            sample_data(verbose=1)
            sleep(1)
            self.temp_data.append(sensor_data['temperature'])
            self.res_data.append(sensor_data['resistance'])
            self.time_data.append(sensor_data['time'])
            self.ui.graph.clear()

            if self.graph_mode == 1:
                self.ui.graph.plot(self.time_data, self.temp_data, pen=mkPen('r', width=10))
            elif self.graph_mode == 2:
                self.ui.graph.plot(self.time_data, self.res_data, pen=mkPen('r', width=10))
            elif self.graph_mode == 3:
                self.ui.graph.plot(self.temp_data, self.res_data, pen=mkPen('r', width=10))

            with open('data.txt', 'a') as f:
                f.write(f'{self.time_data[-1]}, {self.res_data[-1]}, {self.temp_data[-1]}\n')

        # Обработка потери сигнала
        if not sensor_data['res_active']:
            self.console_message = self.ui.console.toPlainText() + "Multimeter disconnected!\n"
            self.notifier.signal_update.emit()
        if not sensor_data['temp_active']:
            self.console_message = self.ui.console.toPlainText() + "Temperature sensor disconnected!\n"
            self.notifier.signal_update.emit()

    # Старт измерений — запускает поток
    def start_measurement(self):
        global should_stop
        thread = Thread(target=self.collect_data)
        thread.start()
        thread.join()
        should_stop = 0

    # Обработка подключения устройств
    def connect_devices(self):
        self.console_message = connect_instruments(connected_devices)
        self.notifier.signal_update.emit()

    def stop_measurement(self):
        global should_stop, time_flag
        should_stop = 1
        time_flag = 1

    def set_temp_graph(self):
        self.graph_mode = 1

    def set_res_graph(self):
        self.graph_mode = 2

    def set_combined_graph(self):
        self.graph_mode = 3

    def set_graph_ranges(self):
        self.res_range = [self.ui.Rmin.value(), self.ui.Rmax.value()]
        self.temp_range = [self.ui.Tmin.value(), self.ui.Tmax.value()]

    # Обновление консоли
    def refresh_console(self):
        if self.console_message:
            updated_text = self.ui.console.toPlainText() + self.console_message
            self.ui.console.setText(updated_text)
            self.console_message = None

# Запуск GUI приложения
if __name__ == '__main__':
    app = QApplication(argv)
    main_window = DataAcquisitionApp()
    main_window.show()
    exit(app.exec())
