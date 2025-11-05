from serial import Serial, SerialException
from .exceptions import InvalidBaudRateException, InvalidCOMPortException

VALID_BAUD_RATE = 110, 300, 600, 1200, 2400, 4800, 9600, 14400, 19200, 38400, 57600, 115200, 128000, 256000
VALID_COM_PORT = (
    [f'COM{i}' for i in range(100)] +
    [f'/dev/tty{i}' for i in range(256)] +
    [f'/dev/ttyS{i}' for i in range(256)] +
    [f'/dev/ttyACM{i}' for i in range(256)]
)

EU433 = "EU433"
CN470 = "CN470"
RU864 = "RU864"
IN865 = "IN865"
EU868 = "EU868"
US915 = "US915"
AU915 = "AU915"
KR920 = "KR920"
AS923_1 = "AS923_1"
AS923_2 = "AS923_2"
AS923_3 = "AS923_3"
AS923_4 = "AS923_4"

VALID_BAND = (EU433, CN470, RU864, IN865, EU868, US915, AU915, KR920, AS923_1, AS923_2, AS923_3, AS923_4)

class RAK4270:
    """
    Interface class for RAK4270 LoRaWAN module communication via AT commands.

    The RAK4270 module uses the at+set_config/at+get_config command format for configuration
    and supports both LoRaWAN and P2P (peer-to-peer) communication modes.

    This class provides a Python interface to:
    - Configure LoRaWAN parameters (DevEUI, AppEUI, AppKey, etc.)
    - Join LoRaWAN networks using OTAA or ABP modes
    - Send and receive LoRaWAN data
    - Configure and use P2P communication
    - Manage serial connection and AT command communication

    Based on RAKwireless official AT command documentation for RAK4270.

    Example:
        >>> rak = RAK4270("COM3")
        >>> rak.connect()
        >>> rak.set_network_mode(True)  # LoRaWAN mode
        >>> rak.set_join_mode(True)     # OTAA mode
        >>> rak.join_network()
        >>> rak.send_lorawan_data(2, "112233")
    """
    def __init__(self, port, baud_rate=9600, timeout=1):
        """
        Initialize RAK4270 instance.

        Args:
            port (str): Serial COM port (e.g., 'COM3', '/dev/ttyUSB0')
            baud_rate (int): Serial baud rate (default: 9600)
            timeout (int): Serial timeout in seconds (default: 1)

        Raises:
            InvalidCOMPortException: If port is not valid
            InvalidBaudRateException: If baud_rate is not valid
        """
        if port not in VALID_COM_PORT:
            raise InvalidCOMPortException('COM port invalid')
        self.port = port
        if baud_rate not in VALID_BAUD_RATE:
            raise InvalidBaudRateException('Baud rate invalid')
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.serial_connection = None
        self.network_mode = None
        self.join_sent = False
        self.frame_sent = True

    def connect(self):
        """Establish serial connection to the RAK4270 module."""
        try:
            self.serial_connection = Serial(self.port, self.baud_rate, timeout=self.timeout)
            print("Connected successfully to", self.port)
        except SerialException as e:
            print("Error connecting to", self.port, ":", e)

    def disconnect(self):
        """Close the serial connection to the RAK4270 module."""
        if self.serial_connection and self.serial_connection.is_open:
            self.serial_connection.close()
            print("Module disconnected")

    def send_command(self, command):
        """
        Send AT command to RAK4270 and return response.

        Args:
            command (str): AT command to send

        Returns:
            str: Response from module, or False/None on error
        """
        if not self.serial_connection or not self.serial_connection.is_open:
            print("Serial connection not established")
            return False

        try:
            self.serial_connection.write((command + '\r\n').encode())
            response = self.serial_connection.readline().decode().strip()
            return response
        except (OSError, UnicodeDecodeError) as e:
            print(f"Error sending command: {e}")
            return None

    def read_response(self):
        if not self.serial_connection or not self.serial_connection.is_open:
            return None

        try:
            response = self.serial_connection.readline().decode().strip()
            return response
        except (OSError, UnicodeDecodeError) as e:
            print(f"Error reading response: {e}")
            return None

    def get_version(self):
        response = self.send_command("at+version")
        if response and "OK" in response:
            return response
        return None

    def restart_device(self):
        response = self.send_command("at+set_config=device:restart")
        return response is not None

    def set_network_mode(self, lorawan=True):
        """
        Set network mode to LoRaWAN or P2P.

        Args:
            lorawan (bool): True for LoRaWAN mode, False for P2P mode

        Returns:
            bool: True if successful, False otherwise
        """
        if lorawan:
            response = self.send_command("at+set_config=lora:work_mode:0")
            self.network_mode = "LoRaWAN"
        else:
            response = self.send_command("at+set_config=lora:work_mode:1")
            self.network_mode = "P2P"

        if response and "OK" in response:
            print(f"Network mode set to {self.network_mode}")
            return True
        print(f"Failed to set network mode to {self.network_mode}")
        return False

    def get_lora_status(self):
        response = self.send_command("at+get_config=lora:status")
        return response

    def set_dev_eui(self, dev_eui):
        if not self.is_only_hex(dev_eui) or len(dev_eui) != 16:
            print("Invalid DevEUI format. Must be 16 hex characters.")
            return False

        response = self.send_command(f"at+set_config=lora:dev_eui:{dev_eui}")
        if response and "OK" in response:
            print(f"DevEUI set to {dev_eui}")
            return True
        print("Failed to set DevEUI")
        return False

    def get_dev_eui(self):
        response = self.send_command("at+get_config=lora:dev_eui")
        if response and "OK" in response:
            return response.split(':')[-1] if ':' in response else response
        return None

    def set_app_eui(self, app_eui):
        if not self.is_only_hex(app_eui) or len(app_eui) != 16:
            print("Invalid AppEUI format. Must be 16 hex characters.")
            return False

        response = self.send_command(f"at+set_config=lora:app_eui:{app_eui}")
        if response and "OK" in response:
            print(f"AppEUI set to {app_eui}")
            return True
        print("Failed to set AppEUI")
        return False

    def get_app_eui(self):
        response = self.send_command("at+get_config=lora:app_eui")
        if response and "OK" in response:
            return response.split(':')[-1] if ':' in response else response
        return None

    def set_app_key(self, app_key):
        if not self.is_only_hex(app_key) or len(app_key) != 32:
            print("Invalid AppKey format. Must be 32 hex characters.")
            return False

        response = self.send_command(f"at+set_config=lora:app_key:{app_key}")
        if response and "OK" in response:
            print(f"AppKey set to {app_key}")
            return True
        print("Failed to set AppKey")
        return False

    def get_app_key(self):
        response = self.send_command("at+get_config=lora:app_key")
        if response and "OK" in response:
            return response.split(':')[-1] if ':' in response else response
        return None

    def set_dev_addr(self, dev_addr):
        if not self.is_only_hex(dev_addr) or len(dev_addr) != 8:
            print("Invalid DevAddr format. Must be 8 hex characters.")
            return False

        response = self.send_command(f"at+set_config=lora:dev_addr:{dev_addr}")
        if response and "OK" in response:
            print(f"DevAddr set to {dev_addr}")
            return True
        print("Failed to set DevAddr")
        return False

    def get_dev_addr(self):
        response = self.send_command("at+get_config=lora:dev_addr")
        if response and "OK" in response:
            return response.split(':')[-1] if ':' in response else response
        return None

    def set_nwks_key(self, nwks_key):
        if not self.is_only_hex(nwks_key) or len(nwks_key) != 32:
            print("Invalid Network Session Key format. Must be 32 hex characters.")
            return False

        response = self.send_command(f"at+set_config=lora:nwks_key:{nwks_key}")
        if response and "OK" in response:
            print("Network Session Key set")
            return True
        print("Failed to set Network Session Key")
        return False

    def get_nwks_key(self):
        response = self.send_command("at+get_config=lora:nwks_key")
        if response and "OK" in response:
            return response.split(':')[-1] if ':' in response else response
        return None

    def set_apps_key(self, apps_key):
        if not self.is_only_hex(apps_key) or len(apps_key) != 32:
            print("Invalid Application Session Key format. Must be 32 hex characters.")
            return False

        response = self.send_command(f"at+set_config=lora:apps_key:{apps_key}")
        if response and "OK" in response:
            print("Application Session Key set")
            return True
        print("Failed to set Application Session Key")
        return False

    def get_apps_key(self):
        response = self.send_command("at+get_config=lora:apps_key")
        if response and "OK" in response:
            return response.split(':')[-1] if ':' in response else response
        return None

    def set_join_mode(self, otaa=True):
        mode = "0" if otaa else "1"  # 0 = OTAA, 1 = ABP
        response = self.send_command(f"at+set_config=lora:join_mode:{mode}")
        if response and "OK" in response:
            mode_str = "OTAA" if otaa else "ABP"
            print(f"Join mode set to {mode_str}")
            return True
        print("Failed to set join mode")
        return False

    def get_join_mode(self):
        response = self.send_command("at+get_config=lora:join_mode")
        if response and "OK" in response:
            return response.split(':')[-1] if ':' in response else response
        return None

    def set_lora_class(self, lora_class):
        if lora_class not in ['A', 'B', 'C']:
            print("Invalid LoRa class. Must be A, B, or C")
            return False

        class_num = {'A': '0', 'B': '1', 'C': '2'}[lora_class]
        response = self.send_command(f"at+set_config=lora:class:{class_num}")
        if response and "OK" in response:
            print(f"LoRa class set to {lora_class}")
            return True
        print("Failed to set LoRa class")
        return False

    def get_lora_class(self):
        response = self.send_command("at+get_config=lora:class")
        if response and "OK" in response:
            return response.split(':')[-1] if ':' in response else response
        return None

    def set_region(self, region):
        if region not in VALID_BAND:
            print(f"Invalid region. Must be one of: {VALID_BAND}")
            return False

        response = self.send_command(f"at+set_config=lora:region:{region}")
        if response and "OK" in response:
            print(f"Region set to {region}")
            return True
        print("Failed to set region")
        return False

    def get_region(self):
        response = self.send_command("at+get_config=lora:region")
        if response and "OK" in response:
            return response.split(':')[-1] if ':' in response else response
        return None

    def join_network(self):
        """
        Send join request to LoRaWAN network.

        Returns:
            bool: True if join request sent successfully, False otherwise
        """
        response = self.send_command("at+join")
        self.join_sent = True
        if response and "OK" in response:
            print("Join request sent")
            return True
        print("Failed to send join request")
        return False

    def check_join_status(self):
        response = self.get_lora_status()
        if response and "joined" in response.lower():
            return True
        return False

    def send_lorawan_data(self, port, payload):
        """
        Send data over LoRaWAN.

        Args:
            port (int): LoRaWAN port (1-223)
            payload (str): Hexadecimal payload string

        Returns:
            bool: True if successful, False otherwise
        """
        if not isinstance(port, int) or port < 1 or port > 223:
            print("Invalid port. Must be between 1 and 223")
            return False

        if not self.is_only_hex(payload):
            print("Invalid payload. Must be hexadecimal string")
            return False

        response = self.send_command(f"at+send=lora:{port}:{payload}")
        self.frame_sent = True
        if response and "OK" in response:
            print(f"Data sent on port {port}")
            return True
        print("Failed to send data")
        return False

    def set_p2p_config(self, freq, sf, bw, cr, preamble, power):  # pylint: disable=R0917
        # Format: freq:sf:bw:cr:preamble:power
        response = self.send_command(f"at+set_config=lorap2p:{freq}:{sf}:{bw}:{cr}:{preamble}:{power}")
        if response and "OK" in response:
            print("P2P configuration set")
            return True
        print("Failed to set P2P configuration")
        return False

    def send_p2p_data(self, payload):
        """
        Send data in P2P mode.

        Args:
            payload (str): Hexadecimal payload string

        Returns:
            bool: True if successful, False otherwise
        """
        if not self.is_only_hex(payload):
            print("Invalid payload. Must be hexadecimal string")
            return False

        response = self.send_command(f"at+send=lorap2p:{payload}")
        if response and "OK" in response:
            print("P2P data sent")
            return True
        print("Failed to send P2P data")
        return False

    def is_only_hex(self, chaine):
        try:
            int(chaine, 16)
            return True
        except ValueError:
            return False

    def get_device_status(self):
        response = self.send_command("at+get_config=device:status")
        return response

    def get_help(self):
        response = self.send_command("at+help")
        return response
