import threading

# Global lock shared by all modules and scripts
serial_lock = threading.Lock()
