"""Irrigação Inteligente com Previsão - ESP32 + MicroPython (Wokwi).

4 plantações, cada uma com seu DHT22 (temperatura), seu sensor de luz (LDR)
e sua bomba (LED azul). A umidade do solo é simulada por um modelo físico:
o solo seca mais rápido com calor e com luz, e a bomba devolve água.
(O Wokwi não tem sensor de umidade de solo que reaja à bomba.)

Diferencial: além de ligar a bomba quando o solo passa do limite mínimo,
o ESP32 ajusta uma reta (regressão linear) nas últimas leituras e liga a
bomba ANTES, se a previsão disser que o solo vai secar em poucos segundos.

Teste de interrupção: no Serial Monitor, envie x + Enter para pausar/retomar
só a transmissão (sensores e bombas continuam funcionando localmente).

Dependências na raiz: config.py, ca_cert.py e mqtt_client.py.
"""
from machine import Pin, ADC
import dht
import time
import network
import ntptime
import ssl
import json
import os
import binascii
import random
import _thread
import sys
import select
import gc
from mqtt_client import MQTTClient
from ca_cert import ca_der
from config import (MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASSWORD,
                    DEVICE_ID, SEND_INTERVAL_MS)

# ---------------------------------------------------------------------------
# Configuração de cada plantação (mesmas faixas da tabela plants no Supabase)
#   secagem: perda base de umidade em %/min a 20 °C e sem luz
#   umid:    umidade inicial do solo (%)
# ---------------------------------------------------------------------------
PLANTAS = [
    {"id": "alface",     "dht": 13, "ldr": 32, "bomba": 18,
     "umid_min": 60, "umid_max": 80, "vazao": 15, "secagem": 6.0, "umid": 71.0},
    {"id": "tomate",     "dht": 14, "ldr": 33, "bomba": 19,
     "umid_min": 55, "umid_max": 75, "vazao": 30, "secagem": 4.5, "umid": 66.0},
    {"id": "morango",    "dht": 27, "ldr": 34, "bomba": 21,
     "umid_min": 65, "umid_max": 80, "vazao": 20, "secagem": 5.2, "umid": 74.0},
    {"id": "manjericao", "dht": 26, "ldr": 35, "bomba": 22,
     "umid_min": 50, "umid_max": 70, "vazao": 12, "secagem": 4.0, "umid": 63.0},
]

HORIZONTE_S = 40      # liga a bomba se a previsão de secar for menor que isso
JANELA = 12           # quantas leituras entram na regressão (uma a cada 2 s)
GANHO_POR_ML = 0.1    # cada ml de água sobe 0,1 ponto de umidade

BOOT_ID = binascii.hexlify(os.urandom(4)).decode()
CLIENT_ID = (DEVICE_ID + "-" + BOOT_ID).encode()
# Prepara o TLS uma vez só, no início, enquanto a memória está livre.
gc.collect()
CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
CTX.verify_mode = ssl.CERT_REQUIRED
CTX.load_verify_locations(cadata=ca_der())
gc.collect()
lock = _thread.allocate_lock()
contador = 0
pausa = False                              # True = transmissão interrompida

# Prepara o hardware e o estado de cada plantação.
for p in PLANTAS:
    p["sensor"] = dht.DHT22(Pin(p["dht"]))
    p["adc"] = ADC(Pin(p["ldr"]))
    p["adc"].atten(ADC.ATTN_11DB)          # faixa de 0 a 3,3 V
    p["led"] = Pin(p["bomba"], Pin.OUT, value=0)
    p["temp"] = 22.0
    p["lux"] = 20000.0
    p["ligada"] = False                    # bomba ligada agora
    p["motivo"] = None                     # "limite" ou "previsao"
    p["historico"] = []                    # [(tempo_s, umidade)] para a regressão
    p["prev_s"] = None                     # previsão atual (s até o mínimo)
    # Acumuladores do intervalo de envio (zerados a cada publicação)
    p["agua_ml"] = 0.0
    p["bomba_no_intervalo"] = False
    p["motivo_intervalo"] = None


def ler_lux(adc):
    """Converte a leitura do LDR do Wokwi em lux (fórmula da documentação do Wokwi)."""
    v = adc.read() / 4095 * 3.3
    v = min(max(v, 0.001), 3.29)
    r = 2000 * v / (1 - v / 3.3)
    lux = (50 * 1e3 * pow(10, 0.7) / r) ** (1 / 0.7)
    lux = lux / 1.8                        # calibração: sensor ligado em 3,3 V
    return round(min(lux, 199999), 1)


def regressao_inclinacao(pontos):
    """Mínimos quadrados: devolve a inclinação (umidade por segundo) da reta."""
    n = len(pontos)
    mx = sum(t for t, _ in pontos) / n
    my = sum(u for _, u in pontos) / n
    sxx = sum((t - mx) ** 2 for t, _ in pontos)
    if sxx == 0:
        return 0.0
    return sum((t - mx) * (u - my) for t, u in pontos) / sxx


def ler_sensores(p):
    try:
        p["sensor"].measure()
        p["temp"] = p["sensor"].temperature()
    except OSError:
        pass  # mantém a última temperatura válida
    p["lux"] = ler_lux(p["adc"])


def atualizar_solo(p, dt):
    """Modelo físico simples do solo para um passo de dt segundos."""
    fator_temp = max(0.2, 1 + 0.05 * (p["temp"] - 20))
    fator_luz = 1 + p["lux"] / 40000
    perda = p["secagem"] / 60 * fator_temp * fator_luz * dt
    ganho = 0.0
    if p["ligada"]:
        ml = p["vazao"] * dt
        ganho = ml * GANHO_POR_ML
        p["agua_ml"] += ml
        if p["umid"] + ganho >= p["umid_max"] - 2:   # desliga assim que encher
            p["ligada"] = False
            p["historico"] = []
    ruido = random.uniform(-0.05, 0.05)
    p["umid"] = min(100.0, max(0.0, p["umid"] - perda + ganho + ruido))


def controlar_bomba(p, agora_s):
    """Decide ligar/desligar a bomba por limite ou por previsão."""
    if p["ligada"]:
        # Desliga quando chega perto do máximo da faixa ideal.
        if p["umid"] >= p["umid_max"] - 2:
            p["ligada"] = False
            p["historico"] = []          # recomeça a regressão após regar
        return
    p["historico"].append((agora_s, p["umid"]))
    p["historico"] = p["historico"][-JANELA:]
    p["prev_s"] = None
    if len(p["historico"]) >= 5:
        incl = regressao_inclinacao(p["historico"])
        if incl < 0:
            p["prev_s"] = max(0.0, (p["umid"] - p["umid_min"]) / -incl)
    if p["umid"] <= p["umid_min"]:
        p["ligada"], p["motivo"] = True, "limite"
    elif p["prev_s"] is not None and p["prev_s"] <= HORIZONTE_S:
        p["ligada"], p["motivo"] = True, "previsao"
    if p["ligada"]:
        p["bomba_no_intervalo"] = True
        p["motivo_intervalo"] = p["motivo"]
        print("BOMBA", p["id"], "LIGADA por", p["motivo"],
              "umidade", round(p["umid"], 1))


def montar_pacotes():
    """Copia o estado de cada plantação e zera os acumuladores do intervalo."""
    global contador
    pacotes = []
    with lock:
        contador += 1
        for p in PLANTAS:
            pacotes.append({
                "device_id": DEVICE_ID,
                "plant_id": p["id"],
                "message_id": "%s-%s-%d" % (p["id"], BOOT_ID, contador),
                "soil_moisture": round(p["umid"], 2),
                "temperature": p["temp"],
                "light_lux": p["lux"],
                "pump_on": p["bomba_no_intervalo"] or p["ligada"],
                "pump_reason": p["motivo_intervalo"] or (p["motivo"] if p["ligada"] else None),
                "water_ml": round(p["agua_ml"], 1),
                "predicted_dry_s": None if p["prev_s"] is None else round(min(p["prev_s"], 3600), 1),
            })
            p["agua_ml"] = 0.0
            p["bomba_no_intervalo"] = False
            p["motivo_intervalo"] = None
    return pacotes


# ---------------------------------------------------------------------------
# Rede: Wi-Fi, relógio, MQTT com TLS e envio periódico (segundo núcleo)
# ---------------------------------------------------------------------------
def conectar(wifi):
    gc.collect()                           # libera memória para o TLS
    if not wifi.isconnected():
        wifi.active(True)
        wifi.connect("Wokwi-GUEST", "")
        inicio = time.ticks_ms()
        while not wifi.isconnected():
            if time.ticks_diff(time.ticks_ms(), inicio) > 40000:
                wifi.disconnect()
                raise OSError("Tempo de Wi-Fi excedido")
            time.sleep_ms(200)
    print("IP", wifi.ifconfig()[0])
    ntptime.timeout = 3
    ntptime.settime()                      # TLS exige relógio correto
    cliente = MQTTClient(client_id=CLIENT_ID, server=MQTT_HOST, port=MQTT_PORT,
                         user=MQTT_USER.encode(), password=MQTT_PASSWORD.encode(),
                         keepalive=60, ssl=CTX)  # contexto TLS criado no início
    cliente.connect(timeout=5)
    print("MQTT CONECTADO")
    return cliente


def tarefa_rede():
    wifi = network.WLAN(network.STA_IF)
    cliente = None
    while True:
        try:
            if pausa:
                # Transmissão interrompida: fecha a conexão e espera a retomada.
                if cliente is not None:
                    try:
                        cliente.sock.close()
                    except Exception:
                        pass
                    cliente = None
                    print("TRANSMISSAO PAUSADA (leituras locais continuam)")
                time.sleep_ms(200)
                continue
            if cliente is None or not wifi.isconnected():
                cliente = conectar(wifi)
            for pacote in montar_pacotes():
                topico = "irrigacao/%s/telemetria" % pacote["plant_id"]
                cliente.publish(topico.encode(), json.dumps(pacote).encode())
            print("PUBLICADO lote", contador)
            time.sleep_ms(SEND_INTERVAL_MS)
        except Exception as erro:
            print("FALHA REDE", type(erro).__name__, str(erro))
            try:
                cliente.sock.close()
            except Exception:
                pass
            cliente = None
            time.sleep_ms(3000)


# ---------------------------------------------------------------------------
# Laço principal: sensores, modelo do solo e controle das bombas
# ---------------------------------------------------------------------------
print("Irrigacao inteligente | 4 plantacoes | previsao por regressao")
print("Envie x + Enter para pausar/retomar a transmissao")
_thread.start_new_thread(tarefa_rede, ())
teclado = select.poll()
teclado.register(sys.stdin, select.POLLIN)
ultimo = time.ticks_ms()
ultima_leitura = 0
while True:
    if teclado.poll(0) and sys.stdin.read(1).lower() == "x":
        pausa = not pausa
        print("PEDIDO", "PAUSA" if pausa else "RETOMADA")
    agora = time.ticks_ms()
    dt = time.ticks_diff(agora, ultimo) / 1000
    ultimo = agora
    with lock:
        for p in PLANTAS:
            atualizar_solo(p, dt)
            p["led"].value(1 if p["ligada"] else 0)
    if time.ticks_diff(agora, ultima_leitura) >= 2000:   # DHT22: no mínimo 2 s
        ultima_leitura = agora
        for p in PLANTAS:
            ler_sensores(p)
        with lock:
            for p in PLANTAS:
                controlar_bomba(p, agora / 1000)
        print(" | ".join("%s %.1f%% %.1fC %dlx%s" % (
            p["id"][:4], p["umid"], p["temp"], p["lux"], " BOMBA" if p["ligada"] else "")
            for p in PLANTAS))
    time.sleep_ms(500)
