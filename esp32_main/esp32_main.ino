#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <DHT.h>

// ====== CẤU HÌNH WIFI & SERVER ======
const char* WIFI_SSID     = "PTIT_B2";
const char* WIFI_PASSWORD = "";
const char* SERVER_URL    = "http://10.18.2.49:8006";  // ipconfig để lấy IP

// ====== CHÂN GPIO — KHỚP VỚI MẠCH ĐÃ LẮP ======
#define DHTPIN    4      // DHT11 DATA → D4
#define DHTTYPE   DHT11
#define LED1      18     // Quạt 1 → D18
#define LED2      19     // Quạt 2 → D19
#define LED3      23     // Quạt 3 → D23
#define LCD_SDA   21     // LCD SDA → D21
#define LCD_SCL   22     // LCD SCL → D22
// LCD VCC nối thẳng vào chân VIN (5V) của ESP32
// LCD GND nối thanh GND breadboard

// ====== KHỞI TẠO ĐỐI TƯỢNG ======
DHT dht(DHTPIN, DHTTYPE);
// ⚠️ Nếu LCD chỉ hiện ô đen: thử đổi 0x3F ↔ 0x27
// Chạy I2C Scanner bên dưới để xác nhận địa chỉ đúng
LiquidCrystal_I2C lcd(0x27, 16, 2);  // Đổi thành 0x3F nếu 0x27 không được

// ====== BIẾN TRẠNG THÁI THIẾT BỊ (nhận từ server) ======
int    fanLevel = 0;       // 0=tắt, 1=mức1, 2=mức2, 3=mức3
bool   acOn     = false;   // Điều hòa bật/tắt
int    acTemp   = 26;      // Nhiệt độ đặt điều hòa
String devMode  = "MANUAL"; // "MANUAL" hoặc "AUTO"

// ====== BIẾN CẢM BIẾN GLOBAL (tránh đọc DHT11 nhiều lần) ======
float  lastTemp = 0.0;     // Nhiệt độ lần đọc gần nhất
float  lastHumi = 0.0;     // Độ ẩm lần đọc gần nhất

// ====== BIẾN THỜI GIAN (non-blocking) ======
unsigned long lastSensorTime  = 0;
unsigned long lastCommandTime = 0;
unsigned long lastWiFiRetryTime = 0;
const unsigned long WIFI_RETRY_INTERVAL = 10000;
bool wifiWasConnected = false;

void setup() {
  Serial.begin(115200);

  // Khởi động I2C với đúng chân SDA=21, SCL=22
  Wire.begin(LCD_SDA, LCD_SCL);

  // Khởi động LED
  pinMode(LED1, OUTPUT);
  pinMode(LED2, OUTPUT);
  pinMode(LED3, OUTPUT);
  digitalWrite(LED1, LOW);
  digitalWrite(LED2, LOW);
  digitalWrite(LED3, LOW);

  // Khởi động DHT11
  dht.begin();

  // Khởi động LCD
  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  lcd.print("He Thong AIoT");
  lcd.setCursor(0, 1);
  lcd.print("Dang ket noi...");

  // Kết nối WiFi
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWiFiRetryTime = millis();
  Serial.println("Dang ket noi WiFi...");
}

void loop() {
  ensureWiFiConnection();
  if (WiFi.status() != WL_CONNECTED) {
    return;  // Giữ nguyên trạng thái LED/LCD gần nhất khi đang offline.
  }

  // Task 1: Gửi sensor lên server mỗi 5 giây
  if (millis() - lastSensorTime > 5000) {
    sendSensorData();
    lastSensorTime = millis();
  }
  // Task 2: Lấy lệnh từ server mỗi 2 giây
  if (millis() - lastCommandTime > 2000) {
    fetchAndApplyState();
    lastCommandTime = millis();
  }
}

// ====== GIỮ KẾT NỐI WIFI, TỰ THỬ LẠI KHI BỊ MẤT ======
void ensureWiFiConnection() {
  if (WiFi.status() == WL_CONNECTED) {
    if (!wifiWasConnected) {
      wifiWasConnected = true;
      Serial.println("WiFi connected: " + WiFi.localIP().toString());
      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("WiFi OK!");
      lcd.setCursor(0, 1);
      lcd.print(WiFi.localIP().toString());
    }
    return;
  }

  if (wifiWasConnected) {
    wifiWasConnected = false;
    Serial.println("Mat ket noi WiFi");
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("WiFi OFFLINE");
    lcd.setCursor(0, 1);
    lcd.print("Dang thu lai...");
  }

  if (millis() - lastWiFiRetryTime >= WIFI_RETRY_INTERVAL) {
    lastWiFiRetryTime = millis();
    Serial.println("Thu ket noi lai WiFi...");
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }
}

// ====== GỬI DỮ LIỆU CẢM BIẾN ======
void sendSensorData() {
  float h = dht.readHumidity();
  float t = dht.readTemperature();
  if (isnan(h) || isnan(t)) {
    Serial.println("Loi doc DHT11!");
    return;
  }
  // Lưu vào biến global để updateLCD dùng lại (không đọc DHT lần 2)
  lastTemp = t;
  lastHumi = h;

  HTTPClient http;
  http.begin(String(SERVER_URL) + "/api/sensor");
  http.addHeader("Content-Type", "application/json");
  StaticJsonDocument<128> doc;
  doc["temperature"] = t;
  doc["humidity"]    = h;
  String body;
  serializeJson(doc, body);
  int code = http.POST(body);
  Serial.printf("POST /sensor -> %d | T=%.1f H=%.1f\n", code, t, h);
  http.end();
}

// ====== LẤY TRẠNG THÁI TỪ SERVER VÀ THỰC THI ======
void fetchAndApplyState() {
  HTTPClient http;
  http.begin(String(SERVER_URL) + "/api/device/state");
  int code = http.GET();
  if (code == 200) {
    String payload = http.getString();
    StaticJsonDocument<256> doc;
    DeserializationError error = deserializeJson(doc, payload);
    if (error) {
      Serial.print("JSON state loi: ");
      Serial.println(error.c_str());
      http.end();
      return;
    }

    if (!doc["fan_level"].is<int>() ||
        !doc["ac_on"].is<bool>() ||
        !doc["ac_temp"].is<int>() ||
        !doc["mode"].is<const char*>()) {
      Serial.println("JSON state thieu hoac sai kieu - bo qua");
      http.end();
      return;
    }

    int newFanLevel = doc["fan_level"].as<int>();
    bool newAcOn    = doc["ac_on"].as<bool>();
    int newAcTemp   = doc["ac_temp"].as<int>();
    String newMode  = doc["mode"].as<String>();

    if (newFanLevel < 0 || newFanLevel > 3 ||
        newAcTemp < 16 || newAcTemp > 32 ||
        (newMode != "MANUAL" && newMode != "AUTO")) {
      Serial.println("JSON state co gia tri khong hop le - bo qua");
      http.end();
      return;
    }

    fanLevel = newFanLevel;
    acOn     = newAcOn;
    acTemp   = newAcTemp;
    devMode  = newMode;
    applyFanLEDs();
    updateLCD();
  } else {
    Serial.printf("GET /device/state -> %d\n", code);
  }
  http.end();
}

// ====== ĐIỀU KHIỂN LED THEO MỨC QUẠT ======
void applyFanLEDs() {
  // Mức 1: LED1 sáng, 2,3 tắt
  // Mức 2: LED1+LED2 sáng, LED3 tắt
  // Mức 3: cả 3 LED sáng
  // Mức 0: tắt hết
  digitalWrite(LED1, fanLevel >= 1 ? HIGH : LOW);
  digitalWrite(LED2, fanLevel >= 2 ? HIGH : LOW);
  digitalWrite(LED3, fanLevel >= 3 ? HIGH : LOW);
}

// ====== CẬP NHẬT LCD ======
void updateLCD() {
  lcd.clear();
  // Dòng 0: Chế độ + Nhiệt độ
  // Dùng lastTemp/lastHumi đã đọc trong sendSensorData — không gọi DHT lần 2
  lcd.setCursor(0, 0);
  String line0 = (devMode == "AUTO" ? "AUTO" : "MAN ");
  line0 += " T:" + String((int)lastTemp) + "C";
  lcd.print(line0.substring(0, 16));
  // Dòng 1: Trạng thái quạt + điều hòa
  lcd.setCursor(0, 1);
  String line1 = "Fan:" + String(fanLevel);
  line1 += " AC:" + String(acOn ? "ON" : "OFF");
  if (acOn) line1 += " " + String(acTemp) + "C";
  lcd.print(line1.substring(0, 16));
}
