#include <Arduino_LED_Matrix.h>
#include <Keyboard.h>
#include <Mouse.h>
#include <WiFiS3.h>
#include <Preferences.h>
#include <tusb.h>
#include <boot.h>
#include <WDT.h>
#include <ctype.h>
#include <errno.h>
#include "serial_control.h"
#include "boot_hid.h"

struct Configuration {
  uint32_t version;
  char ssid[33];
  char password[64];
  char token[33];
};
struct LineBuffer {
  char data[256];
  unsigned used = 0;
  bool invalid = false;
};
struct NamedKey { const char *name; uint8_t usage; };

ArduinoLEDMatrix matrix;
SerialControl serialControl;
Preferences preferences;
Configuration config{}, pending{};
WiFiServer server(4242);
WiFiClient client;
LineBuffer usbLine, networkLine;
KeyReport held{};
uint8_t buttons = 0;
bool authenticated = false, listening = false, releasePending = true;
bool connectPending = false, configured = false;
unsigned long lastValid = 0, lastNetwork = 0, nextConnect = 0, lastWifiCheck = 0;
bool usbWasConnected = false;

const NamedKey namedKeys[] = {
  {"ENTER",0x28},{"ESC",0x29},{"BACKSPACE",0x2a},{"TAB",0x2b},{"SPACE",0x2c},
  {"CAPSLOCK",0x39},{"PRINTSCREEN",0x46},{"SCROLLLOCK",0x47},{"PAUSE",0x48},
  {"INSERT",0x49},{"HOME",0x4a},{"PAGEUP",0x4b},{"DELETE",0x4c},
  {"END",0x4d},{"PAGEDOWN",0x4e},{"RIGHT",0x4f},{"LEFT",0x50},
  {"DOWN",0x51},{"UP",0x52},
  {"CTRL",0xe0},{"SHIFT",0xe1},{"ALT",0xe2},{"GUI",0xe3},
  {"RCTRL",0xe4},{"RSHIFT",0xe5},{"RALT",0xe6},{"RGUI",0xe7}
};

void readCommands(Stream &stream, LineBuffer &line, bool network);
void releaseAll();
void safetyCheck();

void showStatus(char state) {
  static char previous = 0;
  if (state == previous) return;
  previous = state;
  const uint8_t s[] = {14,17,16,14,1,17,14};
  const uint8_t w[] = {17,17,17,21,21,21,10};
  const uint8_t r[] = {30,17,17,30,20,18,17};
  const uint8_t a[] = {14,17,17,31,17,17,17};
  const uint8_t e[] = {31,16,16,30,16,16,31};
  const uint8_t *glyph = state == 'S' ? s : state == 'W' ? w :
                         state == 'R' ? r : state == 'A' ? a : e;
  uint8_t pixels[8][12] = {};
  for (int y=0; y<7; ++y)
    for (int x=0; x<5; ++x) pixels[y][x+3] = (glyph[y] >> (4-x)) & 1;
  matrix.renderBitmap(pixels, 8, 12);
}

static_assert(sizeof(KeyReport) == 8, "Boot keyboard reports must be eight bytes");
bool keyboardReport(const KeyReport &keys) { return sendBootHid(KEYBOARD_INSTANCE, &keys); }
bool mouseReport(int8_t x=0, int8_t y=0, int8_t wheel=0) {
  const int8_t data[] = {int8_t(buttons),x,y,wheel};
  return sendBootHid(MOUSE_INSTANCE, data);
}
bool keysHeld() {
  if (held.modifiers) return true;
  for (uint8_t key : held.keys) if (key) return true;
  return false;
}
void flushRelease() {
  if (!releasePending || !tud_mounted()) return;
  bool keyboardOk = keyboardReport(held);
  bool mouseOk = mouseReport();
  releasePending = !(keyboardOk && mouseOk);
}
void releaseAll() {
  held = KeyReport{};
  buttons = 0;
  releasePending = true;
  flushRelease();
}
void safetyCheck() {
  if ((keysHeld() || buttons) && millis()-lastValid >= 5000) releaseAll();
  if (!tud_mounted()) {
    held = KeyReport{};
    buttons = 0;
    releasePending = true;
  }
}

bool contains(const KeyReport &keys, uint8_t key) {
  if (key >= 0xe0) return keys.modifiers & (1 << (key-0xe0));
  for (uint8_t k : keys.keys) if (k == key) return true;
  return false;
}
bool addKey(KeyReport &keys, uint8_t key) {
  if (contains(keys,key)) return true;
  if (key >= 0xe0) { keys.modifiers |= 1 << (key-0xe0); return true; }
  for (uint8_t &k : keys.keys) if (!k) { k=key; return true; }
  return false;
}
void removeKey(KeyReport &keys, uint8_t key) {
  if (key >= 0xe0) keys.modifiers &= ~(1 << (key-0xe0));
  else for (uint8_t &k : keys.keys) if (k == key) k=0;
}
bool number(const char *text, long minimum, long maximum, long &value, int base=10) {
  if (!text || !*text || isspace(*text)) return false;
  char *end;
  errno=0;
  value=strtol(text,&end,base);
  return !errno && !*end && value>=minimum && value<=maximum;
}
uint8_t keyUsage(const char *name) {
  if (!name) return 0;
  if (strlen(name)==1) {
    char c=toupper(name[0]);
    if (c>='A' && c<='Z') return c-'A'+4;
    if (c>='1' && c<='9') return c-'1'+0x1e;
    if (c=='0') return 0x27;
  }
  for (const auto &key : namedKeys) if (!strcmp(key.name,name)) return key.usage;
  long f;
  if (name[0]=='F' && number(name+1,1,12,f)) return 0x39+f;
  return 0;
}
uint8_t mouseButton(const char *name) {
  if (!name) return 0;
  if (!strcmp(name,"LEFT")) return MOUSE_LEFT;
  if (!strcmp(name,"RIGHT")) return MOUSE_RIGHT;
  if (!strcmp(name,"MIDDLE")) return MOUSE_MIDDLE;
  return 0;
}
bool validToken(const char *text) {
  if (strlen(text)!=32) return false;
  for (unsigned i=0; i<32; ++i) if (!isxdigit(text[i])) return false;
  return true;
}
bool validConfig(const Configuration &c) {
  return c.version==1 && memchr(c.ssid,0,sizeof(c.ssid)) &&
    memchr(c.password,0,sizeof(c.password)) && memchr(c.token,0,sizeof(c.token)) &&
    strlen(c.ssid)>0 && strlen(c.password)>=8 && validToken(c.token);
}
void closeClient() {
  releaseAll();
  authenticated=false;
  networkLine=LineBuffer{};
  if (client) client.stop();
  showStatus(listening ? 'R' : configured ? 'W' : 'S');
}
void stopNetwork() {
  closeClient();
  if (listening) server.end();
  listening=false;
  WiFi.disconnect();
}

const char *configure(char *args) {
  char *value=strchr(args,' ');
  if (value) *value++=0;
  if (!strcmp(args,"SSID") && value && strlen(value)>0 && strlen(value)<=32) {
    // WiFiS3 sends SSID and password as comma-separated AT arguments.
    if (strchr(value,',')) return "ERR BAD_ARGUMENT";
    strcpy(pending.ssid,value);
  } else if (!strcmp(args,"PASSWORD") && value && strlen(value)>=8 && strlen(value)<=63) {
    for (const char *p=value; *p; ++p)
      if (*p<32 || *p>126 || *p==',') return "ERR BAD_ARGUMENT";
    strcpy(pending.password,value);
  } else if (!strcmp(args,"TOKEN") && value && validToken(value)) {
    strcpy(pending.token,value);
  } else if (!strcmp(args,"SAVE") && !value) {
    pending.version=1;
    if (!validConfig(pending)) return "ERR INCOMPLETE_CONFIG";
    stopNetwork();
    Configuration verify{};
    if (!preferences.begin("hid-injector")) return "ERR STORAGE";
    bool ok=preferences.putBytes("config",&pending,sizeof(pending))==sizeof(pending) &&
      preferences.getBytes("config",&verify,sizeof(verify))==sizeof(verify) &&
      !memcmp(&pending,&verify,sizeof(verify));
    preferences.end();
    if (!ok) return "ERR STORAGE";
    config=pending;
    configured=true;
    connectPending=true;
    return "OK SAVED";
  } else if (!strcmp(args,"RESET") && !value) {
    stopNetwork();
    if (!preferences.begin("hid-injector")) return "ERR STORAGE";
    bool ok=preferences.clear();
    preferences.end();
    if (!ok) return "ERR STORAGE";
    config=Configuration{};
    pending=config;
    configured=false;
    connectPending=false;
    showStatus('S');
    return "OK RESET";
  } else return "ERR BAD_ARGUMENT";
  return "OK";
}

const char *hidCommand(char *command, char *args) {
  if (!strcmp(command,"RELEASE_ALL") && !args) { releaseAll(); return "OK"; }
  if (!tud_mounted() || releasePending) return "ERR USB_NOT_READY";
  if (!strcmp(command,"TEXT")) {
    if (!args || strlen(args)>240) return "ERR BAD_ARGUMENT";
    if (keysHeld()) return "ERR KEYS_HELD";
    for (const uint8_t *p=(uint8_t*)args; *p; ++p)
      if (*p>=128 || !KeyboardLayout_en_US[*p]) return "ERR BAD_TEXT";
    lastValid=millis();
    for (const uint8_t *p=(uint8_t*)args; *p; ++p) {
      uint8_t mapped=KeyboardLayout_en_US[*p];
      KeyReport textKeys{};
      textKeys.modifiers=(mapped & 0x80) ? 2 : 0;
      textKeys.keys[0]=mapped & 0x7f;
      if (!keyboardReport(textKeys)) { releaseAll(); return "ERR USB_NOT_READY"; }
      delay(5);
      if (!keyboardReport(held)) { releaseAll(); return "ERR USB_NOT_READY"; }
      safetyCheck();
    }
    return "OK";
  }
  char *words[14];
  unsigned count=0;
  char *save=nullptr;
  for (char *w=args ? strtok_r(args," \t",&save) : nullptr; w; w=strtok_r(nullptr," \t",&save)) {
    if (count==14) return "ERR BAD_ARGUMENT";
    words[count++]=w;
  }
  if (!strcmp(command,"KEY") || !strcmp(command,"DOWN") || !strcmp(command,"UP") ||
      !strcmp(command,"COMBO") || !strcmp(command,"HID")) {
    bool combo=!strcmp(command,"COMBO"), down=!strcmp(command,"DOWN"), up=!strcmp(command,"UP");
    if (!count || (!combo && count!=1)) return "ERR BAD_ARGUMENT";
    uint8_t usages[14];
    KeyReport candidate=held;
    for (unsigned i=0; i<count; ++i) {
      long raw;
      uint8_t key=!strcmp(command,"HID") ?
        (number(words[i],4,0xe7,raw,0) && (raw<=0x73 || raw>=0xe0) ? raw : 0) : keyUsage(words[i]);
      if (!key) return "ERR BAD_KEY";
      for (unsigned j=0; j<i; ++j) if (usages[j]==key) return "ERR BAD_ARGUMENT";
      usages[i]=key;
      if (!down && !up && key<0xe0 && contains(held,key)) return "ERR KEY_HELD";
      if (up) removeKey(candidate,key);
      else if (!addKey(candidate,key)) return "ERR TOO_MANY_KEYS";
    }
    lastValid=millis();
    if (down || up) {
      held=candidate;
      if (!keyboardReport(held)) { releaseAll(); return "ERR USB_NOT_READY"; }
    } else {
      candidate=held;
      for (unsigned i=0; i<count; ++i) {
        addKey(candidate,usages[i]);
        if (!keyboardReport(candidate)) { releaseAll(); return "ERR USB_NOT_READY"; }
        delay(5);
      }
      for (unsigned i=count; i>0; --i) {
        if (!contains(held,usages[i-1])) removeKey(candidate,usages[i-1]);
        if (!keyboardReport(candidate)) { releaseAll(); return "ERR USB_NOT_READY"; }
        delay(5);
      }
    }
    return "OK";
  }
  if (!strcmp(command,"MOVE") || !strcmp(command,"SCROLL")) {
    bool move=!strcmp(command,"MOVE");
    long x=0,y=0,wheel=0;
    if (count!=(move ? 2u : 1u) || !number(words[0],-32767,32767,move ? x : wheel) ||
        (move && !number(words[1],-32767,32767,y))) return "ERR BAD_ARGUMENT";
    lastValid=millis();
    do {
      int dx=constrain(x,-127,127), dy=constrain(y,-127,127), dw=constrain(wheel,-127,127);
      if (!mouseReport(dx,dy,dw)) { releaseAll(); return "ERR USB_NOT_READY"; }
      x-=dx; y-=dy; wheel-=dw;
      safetyCheck();
    } while (x || y || wheel);
    return "OK";
  }
  if (!strcmp(command,"CLICK") || !strcmp(command,"DOWN_MOUSE") || !strcmp(command,"UP_MOUSE")) {
    uint8_t button=count==1 ? mouseButton(words[0]) : 0;
    if (!button) return "ERR BAD_ARGUMENT";
    bool click=!strcmp(command,"CLICK");
    if (click && (buttons & button)) return "ERR BUTTON_HELD";
    lastValid=millis();
    if (!strcmp(command,"UP_MOUSE")) buttons &= ~button;
    else buttons |= button;
    if (!mouseReport()) { releaseAll(); return "ERR USB_NOT_READY"; }
    if (click) {
      delay(5);
      buttons &= ~button;
      if (!mouseReport()) { releaseAll(); return "ERR USB_NOT_READY"; }
    }
    return "OK";
  }
  return "ERR UNKNOWN_COMMAND";
}

void handleCommand(Stream &stream, char *line, bool network) {
  char *args=strchr(line,' ');
  if (args) *args++=0;
  if (network && !authenticated) {
    if (!strcmp(line,"AUTH") && args && !strcmp(args,config.token)) {
      authenticated=true;
      lastNetwork=millis();
      showStatus('A');
      stream.println("OK AUTH");
    } else stream.println("ERR AUTH_REQUIRED");
    return;
  }
  if (!strcmp(line,"CONFIG")) {
    stream.println(network ? "ERR SERIAL_ONLY" : args ? configure(args) : "ERR BAD_ARGUMENT");
    return;
  }
  if ((!strcmp(line,"REBOOT") || !strcmp(line,"BOOTLOADER")) && !args) {
    if (network) { stream.println("ERR SERIAL_ONLY"); return; }
    releaseAll();
    stream.println("OK REBOOTING");
    stream.flush();
    delay(30);
    if (!strcmp(line,"BOOTLOADER")) {
      R_SYSTEM->PRCR=(uint16_t)BSP_PRV_PRCR_PRC1_UNLOCK;
      BOOT_DOUBLE_TAP_DATA=DOUBLE_TAP_MAGIC;
      R_SYSTEM->PRCR=(uint16_t)BSP_PRV_PRCR_LOCK;
    }
    // The watchdog reset avoids the native-USB software-reset failure seen in bring-up.
    // Keep USB detached long enough for the host to notice the mux handover.
    tud_disconnect();
    delay(100);
    WDT.begin(50);
    while (true) {}
  }
  if (!strcmp(line,"STATUS") && !args) {
    lastValid=millis();
    if (network) lastNetwork=lastValid;
    stream.print("OK STATUS configured="); stream.print(configured ? 1 : 0);
    stream.print(" wifi="); stream.print(listening ? "connected" : "disconnected");
    stream.print(" ip="); stream.print(listening ? WiFi.localIP().toString() : "0.0.0.0");
    stream.print(" held="); stream.print((keysHeld() || buttons) ? 1 : 0);
    stream.print(" usb="); stream.println(tud_mounted() ? 1 : 0);
    return;
  }
  const char *result;
  if (!strcmp(line,"PING") && !args) result="OK PONG";
  else result=hidCommand(line,args);
  if (!strncmp(result,"OK",2)) {
    lastValid=millis();
    if (network) lastNetwork=lastValid;
  }
  stream.println(result);
}

void readCommands(Stream &stream, LineBuffer &line, bool network) {
  // A partial line or continuous invalid traffic must not starve release checks.
  for (unsigned budget=0; budget<64 && (network || tud_cdc_connected()) && stream.available(); ++budget) {
    safetyCheck();
    int c=stream.read();
    if (c=='\r') continue;
    if (c=='\n') {
      line.data[line.used]=0;
      if (line.invalid) stream.println("ERR BAD_LINE");
      else handleCommand(stream,line.data,network);
      line=LineBuffer{};
      break;
    } else if (c==0 || c<0 || line.used==sizeof(line.data)-1) line.invalid=true;
    else if (!line.invalid) line.data[line.used++]=c;
  }
}

void setup() {
  matrix.begin();
  showStatus('S');
  SerialUSB.begin(115200);
  Keyboard.begin();
  Mouse.begin();
  if (!beginBootHid()) { showStatus('E'); while (true) delay(100); }
  modem.begin();
  modem.timeout(250);
  WiFi.setTimeout(5000);
  if (preferences.begin("hid-injector")) {
    configured=preferences.getBytes("config",&config,sizeof(config))==sizeof(config) && validConfig(config);
    preferences.end();
  }
  if (!configured) config=Configuration{};
  pending=config;
  connectPending=configured;
}

void loop() {
  safetyCheck();
  flushRelease();
  // SerialUSB::operator bool() calls tud_task(), already run by this core's USB ISR.
  bool serialConnected=tud_cdc_connected();
  if (usbWasConnected && !serialConnected) {
    releaseAll();
    usbLine=LineBuffer{};
    serialControl.discardInput();
  }
  usbWasConnected=serialConnected;
  serialControl.flush();
  if (serialConnected && !serialControl.pendingReply()) readCommands(serialControl,usbLine,false);
  if (configured && (connectPending || (!listening && millis()-nextConnect>=10000))) {
    releaseAll();
    showStatus('W');
    connectPending=false;
    nextConnect=millis();
    if (WiFi.begin(config.ssid,config.password)==WL_CONNECTED) {
      server.begin();
      listening=bool(server);
      showStatus(listening ? 'R' : 'E');
    } else showStatus('E');
  }
  if (!listening) return;
  if (millis()-lastWifiCheck>=1000) {
    lastWifiCheck=millis();
    if (WiFi.status()!=WL_CONNECTED) { stopNetwork(); showStatus('W'); return; }
  }
  if (client && (!client.connected() || millis()-lastNetwork>=30000)) closeClient();
  if (!client) {
    client=server.accept();
    if (client) { authenticated=false; networkLine=LineBuffer{}; lastNetwork=millis(); }
  }
  if (client) readCommands(client,networkLine,true);
}
