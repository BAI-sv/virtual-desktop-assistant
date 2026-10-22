/*
 * esp32_glasses.ino —— 智能眼镜固件 v3.3（按键版）
 *
 * 硬件：ESP32-S3 开发板（OV2640 摄像头）
 *       INMP441 I2S 麦克风（录音）
 *       MAX98357A I2S 功放 + 小喇叭（播放）
 *       1 个按键（GPIO 可配）
 *       可选 4G 模块（SIM7xxx 系 AT 串口，WiFi 连不上时自动拨号）
 *
 * 交互（V3.3 起按键触发，替换早期"凝视 5 秒"方案）：
 *   短按        识别当前画面并翻译文字（/vqa mode=tr）
 *   双击        语音对话（录音→/asr→/chat→/tts→播放）
 *   长按0.8~1.5s 高德导航（/nav，播报距离/时长+前几条指令）
 *   长按1.5~3s   购物点评（/vqa mode=shop）
 *   长按>3s      看图说话（/vqa mode=look，详细描述）
 *
 * 设备发现：开机 POST /device/register 注册，之后每 30 秒心跳；
 *           FairyX 的 glasses_adapter 发现【在线】眼镜，离线不显示。
 *
 * 依赖库：ArduinoJson（bblanchon）、ESP32 自带 WiFiClient/HTTPClient。
 */
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "esp_camera.h"
#include "driver/i2s.h"

// ------------------------------------------------------------- 配置区
const char *WIFI_SSID = "你的WiFi";          // ← 改成你的 WiFi
const char *WIFI_PASS = "你的密码";            // ← 改成你的密码

// 眼镜 AI 中枢地址（WiFi 下用局域网 IP；4G 下必须公网 IP/域名）
const char *CENTER_HOST = "192.168.1.100";    // ← 跑 ai_center.py 那台机器
const uint16_t CENTER_PORT = 5000;
const char *DEVICE_NAME = "智能眼镜";           // 设备池里显示的名字
const char *NAV_DEST = "目的地";                // 长按导航的默认目的地，改这里

// 按键
#define KEY_GPIO      4
#define DEBOUNCE_MS   30
#define LONG_MS       800     // 0.8s 起算长按
#define LONG2_MS      1500    // 1.5s 购物
#define LONG3_MS      3000    // 3s 看图
#define DBLCLK_MS     350     // 双击间隔

// 麦克风 / 功放（ESP32-S3 I2S）
#define I2S_BCK   41
#define I2S_WS    42
#define I2S_DIN   2    // INMP441 数据
#define I2S_DOUT  3    // MAX98357A 数据

// 4G（可选；WiFi 失败自动用）
#define USE_4G          1
#define MODEM_TX        17
#define MODEM_RX        16
#define MODEM_BAUD      115200

// ------------------------------------------------------------- 全局
static const char *TAG = "glasses";
static String dev_id = "";
static bool on4g = false;
static bool busy = false;

// 按键状态机
static int  keyState = HIGH;
static int  lastKeyState = HIGH;
static unsigned long pressMs = 0, lastReleaseMs = 0, lastPressMs = 0;
static int  pressCount = 0;
static bool longFired = false;

// ------------------------------------------------------------- I2S
static void i2s_init_mic() {
  i2s_config_t cfg = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = 16000,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_RIGHT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 1024,
  };
  i2s_pin_config_t pin = {.bck_io_num = I2S_BCK, .ws_io_num = I2S_WS,
                          .data_out_num = I2S_PIN_NO_CHANGE, .data_in_num = I2S_DIN};
  i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL);
  i2s_set_pin(I2S_NUM_0, &pin);
}

static void i2s_init_speaker() {
  i2s_config_t cfg = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = 16000,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_RIGHT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 1024,
  };
  i2s_pin_config_t pin = {.bck_io_num = I2S_BCK, .ws_io_num = I2S_WS,
                          .data_out_num = I2S_DOUT, .data_in_num = I2S_PIN_NO_CHANGE};
  i2s_driver_install(I2S_NUM_1, &cfg, 0, NULL);
  i2s_set_pin(I2S_NUM_1, &pin);
}

// 录 N 秒：16kHz 16bit 单声道，加 WAV 头
static String record_wav(int seconds) {
  size_t total = 16000 * 2 * seconds;
  uint8_t *buf = (uint8_t *)malloc(total + 44);
  if (!buf) return "";
  uint32_t *w = (uint32_t *)(buf + 0);
  memcpy(buf, "RIFF", 4); w[1] = 36 + total; memcpy(buf + 8, "WAVEfmt ", 8);
  w[3] = 16; uint16_t *h = (uint16_t *)(buf + 20);
  h[0] = 1; h[1] = 1; w[4] = 16000; w[5] = 32000;
  h[4] = 2; h[5] = 16; memcpy(buf + 36, "data", 4); w[9] = total;
  size_t got = 0;
  while (got < total) {
    size_t n = 0;
    i2s_read(I2S_NUM_0, buf + 44 + got, total - got, &n, portMAX_DELAY);
    got += n;
  }
  String s = String((char *)buf, total + 44);
  free(buf);
  return s;
}

static void play_wav(const uint8_t *wav, size_t len) {
  size_t off = 44;
  if (len < 44 || memcmp(wav, "RIFF", 4)) {           // 非 WAV：当裸 PCM 播
    off = 0;
  }
  size_t remain = len - off;
  while (remain > 0) {
    size_t n = 0;
    i2s_write(I2S_NUM_1, wav + off + (len - off - remain), remain, &n, portMAX_DELAY);
    remain -= n;
  }
  delay(200);
}

// ------------------------------------------------------------- 摄像头
static bool cam_init() {
  camera_config_t cfg;
  cfg.ledc_channel = LEDC_CHANNEL_0;
  cfg.ledc_timer = LEDC_TIMER_0;
  cfg.pin_d0 = 15; cfg.pin_d1 = 16; cfg.pin_d2 = 17; cfg.pin_d3 = 18;
  cfg.pin_d4 = 12; cfg.pin_d5 = 10; cfg.pin_d6 = 8; cfg.pin_d7 = 9;
  cfg.pin_xclk = 11; cfg.pin_pclk = 13; cfg.pin_vsync = 6; cfg.pin_href = 7;
  cfg.pin_sccb_sda = 4; cfg.pin_sccb_scl = 5;   // ← 按键冲突时改这里
  cfg.pin_pwdn = -1; cfg.pin_reset = -1;
  cfg.xclk_freq_hz = 20000000;
  cfg.pixel_format = PIXFORMAT_JPEG;
  cfg.frame_size = FRAMESIZE_VGA;              // 640x480，够识别又小
  cfg.jpeg_quality = 12;
  cfg.fb_count = 1;
  esp_err_t e = esp_camera_init(&cfg);
  return e == ESP_OK;
}

static bool cam_snap(uint8_t **out, size_t *len) {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return false;
  *out = fb->buf; *len = fb->len;
  return true;
}

static void cam_release() { esp_camera_fb_return(esp_camera_fb_get()); }

// ------------------------------------------------------------- 4G（可选）
static HardwareSerial modem(2);

static bool modem_cmd(const char *cmd, const char *expect, int ms) {
  modem.println(cmd);
  unsigned long t = millis();
  String r;
  while (millis() - t < ms) {
    while (modem.available()) {
      r += (char)modem.read();
      if (expect && r.indexOf(expect) >= 0) return true;
    }
    delay(10);
  }
  return r.length() > 0;
}

static bool modem_http_post_text(const char *path, const String &body, String &resp) {
  String url = String("http://") + CENTER_HOST + ":" + CENTER_PORT + path;
  String cmd = String("AT+HTTPCLIENT=1,\"POST\",\"") + url + "\",\"" + body + "\",\"15\"";
  modem.println(cmd);
  unsigned long t = millis();
  while (millis() - t < 20000) {
    while (modem.available()) {
      char c = modem.read();
      if (c == '\n') {                          // HTTPCLIENT 返回一行响应
        resp = modem.readStringUntil('\n');
        return true;
      }
    }
    delay(10);
  }
  return false;
}

static bool connect_4g() {
  modem.begin(MODEM_BAUD, SERIAL_8N1, MODEM_TX, MODEM_RX);
  delay(500);
  modem_cmd("AT", "OK", 2000);
  modem_cmd("AT+CFUN=1", "OK", 3000);
  modem_cmd("AT+CGDCONT=1,\"IP\",\"cmnet\"", "OK", 3000);
  modem_cmd("AT+CGACT=1,1", "OK", 10000);
  on4g = true;
  return true;
}

// ------------------------------------------------------------- HTTP
static String http_post(const String &path, const String &body,
                        const uint8_t *raw, size_t rawlen,
                        const char *ctype) {
  if (on4g) {
    // 4G 只有文本能力（AT 传图不可行）
    if (raw) return "{\"ok\":false,\"error\":\"4G 模式不支持图片，请连 WiFi\"}";
    String resp;
    if (modem_http_post_text(path.c_str(), body, resp)) return resp;
    return "{\"ok\":false,\"error\":\"4G HTTP 失败\"}";
  }
  WiFiClient c;
  HTTPClient h;
  String url = String("http://") + CENTER_HOST + ":" + CENTER_PORT + path;
  bool ok = false;
  if (raw) ok = h.begin(c, url), h.addHeader("Content-Type", ctype),
              h.POST((const uint8_t *)raw, rawlen);
  else     ok = h.begin(c, url), h.addHeader("Content-Type", "application/json"),
              h.POST(body);
  if (!ok) { h.end(); return "{\"ok\":false,\"error\":\"HTTP 连接失败\"}"; }
  String r = h.getString();
  h.end();
  return r;
}

static String http_post_json(const String &path, const String &body) {
  return http_post(path, body, NULL, 0, "application/json");
}

static String http_post_raw(const String &path, const uint8_t *raw, size_t n,
                            const char *ctype) {
  return http_post(path, "", raw, n, ctype);
}

// ------------------------------------------------------------- 请求
static String json_str(const String &resp, const char *key) {
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, resp)) return "";
  return String((const char *)doc[key] | "");
}

// 心跳：每 30 秒注册一次（FairyX 据此判定在线）
static void device_beat() {
  StaticJsonDocument<256> d;
  d["device_id"] = dev_id;
  d["name"] = DEVICE_NAME;
  d["type"] = "glasses";
  d["ip"] = on4g ? "4G" : WiFi.localIP().toString();
  String body;
  serializeJson(d, body);
  http_post_json("/device/register", body);
}

// 短按：识别画面并翻译
static void do_see_translate() {
  uint8_t *img; size_t n;
  if (!cam_snap(&img, &n)) { Serial.println("[cam] fail"); return; }
  String r = http_post_raw("/vqa?mode=tr", img, n, "image/jpeg");
  cam_release();
  String text = json_str(r, "text");
  Serial.println("[vqa-tr] " + text);
  if (text.length()) {
    String b; StaticJsonDocument<256> d; d["text"] = text; serializeJson(d, b);
    String w = http_post_json("/tts", b);
    play_wav((const uint8_t *)w.c_str(), w.length());
  }
}

// 双击：语音对话
static void do_voice_chat() {
  String wav = record_wav(5);
  if (!wav.length()) return;
  String r = http_post_raw("/asr", (const uint8_t *)wav.c_str(), wav.length(),
                           "audio/wav");
  String q = json_str(r, "text");
  Serial.println("[asr] " + q);
  if (!q.length()) return;
  String b; StaticJsonDocument<256> d; d["text"] = q; serializeJson(d, b);
  String a = http_post_json("/chat", b);
  String ans = json_str(a, "text");
  Serial.println("[chat] " + ans);
  if (ans.length()) {
    String b2; StaticJsonDocument<256> d2; d2["text"] = ans; serializeJson(d2, b2);
    String w2 = http_post_json("/tts", b2);
    play_wav((const uint8_t *)w2.c_str(), w2.length());
  }
}

// 长按 0.8~1.5s：导航（默认目的地 = 配置区 NAV_DEST）
static void do_nav() {
  String r = http_post_json("/nav?destination=" + String(NAV_DEST), "{}");
  StaticJsonDocument<1024> doc;
  if (deserializeJson(doc, r)) return;
  const char *km = doc["distance_km"] | "0";
  const char *min = doc["duration_min"] | "0";
  String text = String("导航中，约") + km + "公里，" + min + "分钟。";
  JsonArray st = doc["steps"].as<JsonArray>();
  int i = 0;
  for (JsonVariant s : st) { if (i++ >= 3) break; text += String((const char *)s) + "。"; }
  String b; StaticJsonDocument<256> d; d["text"] = text; serializeJson(d, b);
  String w = http_post_json("/tts", b);
  play_wav((const uint8_t *)w.c_str(), w.length());
}

// 长按 1.5~3s：购物点评
static void do_shop() {
  uint8_t *img; size_t n;
  if (!cam_snap(&img, &n)) return;
  String r = http_post_raw("/vqa?mode=shop", img, n, "image/jpeg");
  cam_release();
  String text = json_str(r, "text");
  if (text.length()) {
    String b; StaticJsonDocument<256> d; d["text"] = text; serializeJson(d, b);
    String w = http_post_json("/tts", b);
    play_wav((const uint8_t *)w.c_str(), w.length());
  }
}

// 长按 >3s：看图说话
static void do_look() {
  uint8_t *img; size_t n;
  if (!cam_snap(&img, &n)) return;
  String r = http_post_raw("/vqa?mode=look", img, n, "image/jpeg");
  cam_release();
  String text = json_str(r, "text");
  if (text.length()) {
    String b; StaticJsonDocument<256> d; d["text"] = text; serializeJson(d, b);
    String w = http_post_json("/tts", b);
    play_wav((const uint8_t *)w.c_str(), w.length());
  }
}

// ------------------------------------------------------------- 按键状态机
static void key_loop() {
  int now = digitalRead(KEY_GPIO);
  if (now != lastKeyState) {
    delay(DEBOUNCE_MS);
    now = digitalRead(KEY_GPIO);
    if (now != lastKeyState) {
      lastKeyState = now;
      if (now == LOW) {                       // 按下
        pressMs = millis();
        longFired = false;
        if (pressCount == 0 || millis() - lastPressMs > DBLCLK_MS * 2) {
          pressCount = 0;
        }
        pressCount++;
        lastPressMs = millis();
      } else {                                // 松开
        unsigned long held = millis() - pressMs;
        if (held < LONG_MS) {                 // 短按
          if (pressCount == 1) {
            busy = true; do_see_translate(); busy = false;
          } else if (pressCount == 2) {
            busy = true; do_voice_chat(); busy = false;
          }
          pressCount = 0;
        }
        lastReleaseMs = millis();
      }
    }
  }
  // 长按判定（按住不放）
  if (lastKeyState == LOW && !longFired && (millis() - pressMs) > LONG_MS) {
    longFired = true;
    busy = true;
    if ((millis() - pressMs) > LONG3_MS)      do_look();
    else if ((millis() - pressMs) > LONG2_MS) do_shop();
    else                                      do_nav();
    busy = false;
    pressCount = 0;
  }
  // 双击补判：短按后等双击窗口
  if (pressCount == 1 && (millis() - lastPressMs) > DBLCLK_MS * 2
      && (millis() - lastPressMs) > 0 && (millis() - lastReleaseMs) > DBLCLK_MS) {
    // 单按已处理（见松开分支），这里只清残留
  }
}

// ------------------------------------------------------------- setup/loop
void setup() {
  Serial.begin(115200);
  pinMode(KEY_GPIO, INPUT_PULLUP);
  i2s_init_mic();
  i2s_init_speaker();
  cam_init();
  dev_id = String((uint32_t)ESP.getEfuseMac(), HEX);

  Serial.printf("[%s] 开机，设备ID: %s\n", TAG, dev_id.c_str());
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 20 && WiFi.status() != WL_CONNECTED; i++) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[%s] WiFi 已连接: %s\n", TAG, WiFi.localIP().toString().c_str());
    on4g = false;
  } else if (USE_4G) {
    Serial.println("\n[%s] WiFi 失败，拨号 4G");
    connect_4g();
  }
  device_beat();
}

void loop() {
  key_loop();
  static unsigned long lastBeat = 0;
  if (millis() - lastBeat > 30000) {          // 30 秒心跳
    lastBeat = millis();
    device_beat();
  }
  delay(10);
}
