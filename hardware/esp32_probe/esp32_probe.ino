// ColdSense-DQ live probe: ESP32 + DS18B20 pushing to the ingestion API.
//
// One reading every REPORT_INTERVAL_S (300 s, the rate every quality threshold was calibrated
// at), sent to POST /api/ingest/readings/batch with the device key in X-Device-Key.
//
// Store-and-forward. Every reading goes into a ring buffer first and leaves it only when the
// API has confirmed it (HTTP 201). Wi-Fi down, API down, router rebooting: the readings wait
// and go up as one batch when the link is back, with their original timestamps. The API
// ignores an exact repeat, so a batch resent after a lost acknowledgement is harmless.
//
// Timestamps. Each reading is stamped from NTP. A reading taken before the clock first syncs
// (just after power-up) keeps its millis() value and is stamped when the clock is known, by
// counting back from the current time. Nothing is sent until then: letting the server stamp a
// backlog on arrival would put every buffered reading at the same, wrong, moment.
//
// What is deliberately NOT done here. The probe does not judge its readings. A plausible but
// wrong value (a probe lying on the compressor) is sent as measured, because catching it is
// the quality layer's job. Only the DS18B20's own "no reading" sentinels are dropped: -127 C
// (probe disconnected) and the 85 C power-on value; those are not measurements, and a probe
// that keeps producing them goes silent, which the dashboard reports as silence.
//
// Wiring and setup: see README.md in this folder. Copy config.example.h to config.h first.

#include <WiFi.h>
#include <HTTPClient.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <time.h>

#include "config.h"

#ifndef BUFFER_LEN
#define BUFFER_LEN 288  // 24 hours at 5-minute reporting
#endif
#define BATCH_MAX 48                       // readings per POST; keeps the JSON body under ~4 KB
#define REFUSED_BACKOFF_MS (10UL * 60000)  // after a 401/404/422, retry rarely: each one is a security-log entry

struct Reading {
  time_t epoch;        // 0 until the clock is known
  uint32_t takenAtMs;  // millis() when sampled, to back-date pre-sync readings
  float tempC;
};

static Reading ring[BUFFER_LEN];
static uint16_t head = 0, count = 0;  // oldest reading is ring[head]
static uint32_t dropped = 0;

static OneWire oneWire(ONE_WIRE_PIN);
static DallasTemperature probe(&oneWire);

static uint32_t lastSampleMs = 0;
static uint32_t backoffUntilMs = 0;
static bool firstSample = true;

static bool clockSynced() { return time(nullptr) > 1700000000; }  // after Nov 2023: NTP has answered

static void push(const Reading &r) {
  if (count == BUFFER_LEN) {  // full: the oldest reading gives way
    head = (head + 1) % BUFFER_LEN;
    count--;
    dropped++;
  }
  ring[(head + count) % BUFFER_LEN] = r;
  count++;
}

static void sample() {
  probe.requestTemperatures();
  float t = probe.getTempCByIndex(0);
  bool powerOnValue = firstSample && t == 85.0f;
  firstSample = false;
  if (t == DEVICE_DISCONNECTED_C || powerOnValue) {
    Serial.printf("probe gave no reading (%.1f), skipped\n", t);
    return;
  }
  Reading r = {clockSynced() ? time(nullptr) : 0, millis(), t};
  push(r);
  Serial.printf("sampled %.4f C, %u buffered\n", t, count);
}

static void isoTime(time_t epoch, char *out, size_t n) {
  struct tm tm;
  gmtime_r(&epoch, &tm);
  strftime(out, n, "%Y-%m-%dT%H:%M:%SZ", &tm);
}

// Send up to BATCH_MAX of the oldest readings. Returns true when they were accepted.
static bool flushOnce() {
  uint16_t n = count < BATCH_MAX ? count : BATCH_MAX;
  time_t now = time(nullptr);
  uint32_t nowMs = millis();

  String body;
  body.reserve(64 + n * 80);
  body += "{\"readings\":[";
  for (uint16_t i = 0; i < n; i++) {
    Reading &r = ring[(head + i) % BUFFER_LEN];
    time_t ts = r.epoch ? r.epoch : now - (time_t)((nowMs - r.takenAtMs) / 1000);  // unsigned: wrap-safe
    char iso[25], item[128];
    isoTime(ts, iso, sizeof iso);
    snprintf(item, sizeof item, "%s{\"device_id\":\"%s\",\"ts\":\"%s\",\"temp\":%.4f}", i ? "," : "", DEVICE_ID, iso, r.tempC);
    body += item;
  }
  body += "]}";

  HTTPClient http;
  http.begin(String(API_BASE) + "/ingest/readings/batch");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", DEVICE_KEY);
  http.setTimeout(15000);
  int code = http.POST(body);
  String reply = code > 0 ? http.getString() : http.errorToString(code);
  http.end();

  if (code == 201) {
    head = (head + n) % BUFFER_LEN;
    count -= n;
    Serial.printf("sent %u, %u still buffered\n", n, count);
    return true;
  }
  if (code >= 400 && code < 500) {
    // refused for who we are (wrong key, unregistered ID), not for the network: no point
    // retrying every cycle. Keep the readings; fix config.h or register the device.
    Serial.printf("REFUSED %d: %s\n", code, reply.c_str());
    backoffUntilMs = millis() + REFUSED_BACKOFF_MS;
  } else {
    Serial.printf("send failed (%d: %s), holding %u\n", code, reply.c_str(), count);
  }
  return false;
}

static void flush() {
  if (!count || WiFi.status() != WL_CONNECTED || !clockSynced()) return;
  if (backoffUntilMs && (int32_t)(millis() - backoffUntilMs) < 0) return;
  backoffUntilMs = 0;
  while (count && flushOnce()) {
  }
  if (dropped) {
    Serial.printf("note: %lu reading(s) were lost to a full buffer\n", (unsigned long)dropped);
    dropped = 0;
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.printf("\nColdSense probe %s -> %s\n", DEVICE_ID, API_BASE);

  probe.begin();
  probe.setResolution(12);  // 0.0625 C, ~750 ms per conversion
  if (probe.getDeviceCount() == 0) Serial.println("WARNING: no DS18B20 found; check the wiring and the 4.7k pull-up");

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(0, 0, "pool.ntp.org", "time.google.com");  // UTC; the API stores UTC

  lastSampleMs = millis() - REPORT_INTERVAL_S * 1000UL;  // first reading straight away
}

void loop() {
  if (millis() - lastSampleMs >= REPORT_INTERVAL_S * 1000UL) {
    lastSampleMs += REPORT_INTERVAL_S * 1000UL;  // fixed schedule, no drift from send time
    sample();
  }
  static uint32_t lastFlushMs = 0;
  if (millis() - lastFlushMs >= 5000) {  // a backlog drains within seconds of the link returning
    lastFlushMs = millis();
    flush();
  }
  delay(50);
}
