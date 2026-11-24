// Copy this file to config.h (which git ignores) and fill it in. Never commit config.h:
// it holds your Wi-Fi password and the device key.

#define WIFI_SSID "your-wifi-name"
#define WIFI_PASSWORD "your-wifi-password"

// The computer running the API, as the ESP32 sees it on the Wi-Fi network (not localhost).
// Start the API with --host 0.0.0.0 so it accepts connections from the network.
#define API_BASE "http://192.168.1.20:8000/api"

// Must match a unit registered on the dashboard's Devices page, exactly.
#define DEVICE_ID "LIVE-001-MS"

// DEVICE_API_KEY from the project's .env file.
#define DEVICE_KEY "paste-the-device-key-here"

#define ONE_WIRE_PIN 4          // DS18B20 data line (yellow)
#define REPORT_INTERVAL_S 300   // the rate the quality thresholds are calibrated for
#define BUFFER_LEN 288          // readings held while offline: 24 h at 5 minutes
