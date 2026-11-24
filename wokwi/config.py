# Configuração do ESP32. A senha MQTT NÃO fica no código público:
# antes de rodar, troque COLE_A_SENHA_MQTT pela senha do usuário irrigacao_esp32
# e não salve o projeto com ela.
MQTT_HOST = "sbe6bf6f.ala.us-east-1.emqxsl.com"
MQTT_PORT = 8883
MQTT_USER = "irrigacao_esp32"
MQTT_PASSWORD = "COLE_A_SENHA_MQTT"
DEVICE_ID = "irrig01"
SEND_INTERVAL_MS = 5000
