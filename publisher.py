import time
import json
import random
import pandas as pd
from paho.mqtt import client as mqtt_client

# TODO: UPDATE PATH CSV_FILE IN HERE --- r"/Synthetic-Data Path "
File_csv_Path = r"C:\Users\idtcu\Alpha-I\Data\synthetic_plant_train.csv"

# NOTE: MQTT Configuration
MQTT_CONFIG = {
    "BROKER" : 'broker.emqx.io',
    "PORT"   : 1883,
    "TOPIC": "idt/plant/env",
    "CLIENT_ID" : f'ALPHA-I-{random.randint(0,100)}'
}

def connect_mqtt():
    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1, MQTT_CONFIG["CLIENT_ID"])
    client.on_connect = lambda client, userdata, flags, rc: print("CONNECTED TO MQTT BROKER!") if rc ==0 else print("FAILED TO CONNECT")
    client.connect(MQTT_CONFIG["BROKER"], MQTT_CONFIG["PORT"], 120)
    return client

def publisher(PUBLISH_INTERVAL_SEC= 0.5):
    df = pd.read_csv(File_csv_Path)
    df = df.iloc[:100]
    print(f"Loaded {len(df)} rows from {File_csv_Path}")
    client = connect_mqtt()
    client.loop_start()

    try:
        for idx, row in df.iterrows():
            data = row.to_dict()
            if "timestamp" in data:
                data["timestamp"] = str(data["timestamp"])

            payload = json.dumps(data)
            result = client.publish(MQTT_CONFIG["TOPIC"], payload)
            status = result[0]
            if status == 0:
                print(f"[{idx}] Published to {MQTT_CONFIG["TOPIC"]}: {payload}")
            else:
                print(f"[{idx}] Failed to send message to {MQTT_CONFIG["TOPIC"]}")

            time.sleep(PUBLISH_INTERVAL_SEC)

    except KeyboardInterrupt:
        print("\nStopping publisher...")

    finally:
        client.loop_stop()
        client.disconnect()
        print("Disconnected from MQTT broker.")


if __name__ == "__main__":
    publisher()
