import json
import websocket
import threading
from datetime import datetime
from collections import defaultdict

class SimplePocketOption:
    def __init__(self, ssid):
        self.ssid = ssid
        self.ws = None
        self.connected = False
        self.candles = defaultdict(list)
        self.current_prices = {}
        
    def connect(self):
        try:
            self.ws = websocket.WebSocketApp(
                "wss://api.pocketoption.com/events/",
                on_message=self.on_message,
                on_open=self.on_open,
                on_error=self.on_error,
                on_close=self.on_close
            )
            
            wst = threading.Thread(target=self.ws.run_forever)
            wst.daemon = True
            wst.start()
            
            # Wait for connection
            import time
            for _ in range(10):
                if self.connected:
                    return True, "Connected successfully"
                time.sleep(0.5)
                
            return False, "Connection timeout"
            
        except Exception as e:
            return False, str(e)
    
    def on_open(self, ws):
        print("WebSocket opened, sending auth...")
        ws.send(self.ssid)
    
    def on_message(self, ws, message):
        try:
            if message.startswith('42'):
                data = json.loads(message[2:])
                event_type = data[0] if data else None
                
                if event_type == "auth":
                    print(f"✅ Authenticated: {data[1]}")
                    self.connected = True
                    
                elif event_type == "candles":
                    # Handle candle data
                    candle_data = data[1]
                    print(f"📊 Candle data: {candle_data}")
                    
                elif event_type == "price":
                    # Handle price updates
                    price_data = data[1]
                    pair = price_data.get('asset')
                    price = price_data.get('value')
                    if pair and price:
                        self.current_prices[pair] = float(price)
                        
        except Exception as e:
            print(f"Message error: {e}")
    
    def on_error(self, ws, error):
        print(f"❌ WebSocket error: {error}")
    
    def on_close(self, ws, close_status_code, close_msg):
        print(f"WebSocket closed: {close_status_code} - {close_msg}")
        self.connected = False
    
    def subscribe_candles(self, pair):
        if self.ws:
            msg = f'42["subscribe",{{"asset":"{pair}","period":60}}]'
            self.ws.send(msg)
            print(f"📡 Subscribed to {pair}")