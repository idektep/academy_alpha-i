import json
import os
import csv
import paho.mqtt.client as mqtt
import numpy as np
from xgboost import XGBClassifier

BROKER_HOST = "broker.emqx.io"
BROKER_PORT = 1883

TOPIC_IN = "plant/env/raw"
TOPIC_OUT = "plant/env/predicted"

MODEL_PATH = r"D:\work\idektep\academy\Data\xgb_plant_model.json"
OUTPUT_CSV = "predicted_result_1.csv"

label_map = {0: "normal", 1: "alert", 2: "alarm"}

fieldnames = None
writer_initialized = False

def load_model():
    model = XGBClassifier()
    model.load_model(MODEL_PATH)
    print(f"Loaded model from {MODEL_PATH}")
    return model

model = load_model()

def save_prediction_row(data: dict):
    global fieldnames, writer_initialized
    
    if not writer_initialized:
        fieldnames = list(data.keys())
        
        file_exits = os.path.isfile(OUTPUT_CSV)
        with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exits:
                writer.writeheader()
            writer.writerow(data)
            
        writer_initialized = True
    else:
        with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writerow(data)
            
def on_connect (client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT broker.")
        client.subscribe(TOPIC_IN)
        print(f"Subscribed to {TOPIC_IN}")
    else:
        print(f"Failed to connect, rc={rc}")

def on_message (client, userdata, msg):
    try:
        payload_str = msg.payload.decode("utf-8")
        data = json.loads(payload_str)

        temp = data.get("temp_c", None)
        hum = data.get("humidity_pct", None)
        lux = data.get("lux", None)
        vpd = data.get("vpd_kpa", None)

        if None in (temp, hum, lux, vpd):
            print("Missing features in message, skipping:", data)
            return
        
        x_input = np.array([[temp, hum, lux, vpd]], dtype=float)
        y_pred = model.predict(x_input)[0]
        y_prob = model.predict_proba(x_input)[0]
        
        label = label_map.get(int(y_pred), "unknown")
        
        data["y_pred"] = int(y_pred)
        data["y_label_pred"] = label
        data["y_pred_prob_normal"] = float(y_prob[0])
        data["y_pred_prob_alert"] = float(y_prob[1])
        data["y_pred_prob_alarm"] = float(y_prob[2])
        
        if "timestamp" in data:
            data["timestamp"] = str(data["timestamp"])
        
        out_payload = json.dumps(data)
        client.publish(TOPIC_OUT, out_payload)
        
        save_prediction_row(data)
        print(f"Saved prediction to {OUTPUT_CSV}")
        
    except Exception as e:
        print("Error in on_message", e)


def main():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    print("Starting predictior loop. Ctrl+C to stop.")

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print('\nStopping predictor...')
    finally:
        client.disconnect()
        print("Disconnected from MQTT broker.")
        
        
if __name__ == "__main__":
    main()     