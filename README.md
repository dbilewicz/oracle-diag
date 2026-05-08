# oracle_diag – instrukcja przygotowania środowiska

## Wymagania systemowe

- Python 3.8+
- Oracle 11g: wymagany **Oracle Instant Client Basic** (nie Basic Light)
- Oracle 12.1+: opcjonalny (thin mode działa bez Instant Client)
- System: Linux x86_64 (Debian/Ubuntu/RHEL)

---

## 1. Przygotowanie venv

```bash
# Utwórz środowisko wirtualne
python3 -m venv /opt/venv/oracle-diag

# Aktywuj
source /opt/venv/oracle-diag/bin/activate

# Zainstaluj sterownik
pip install oracledb

# Sprawdź
python3 -c "import oracledb; print(oracledb.__version__)"
```

Aby wyjść z venv: `deactivate`

---

## 2. Oracle Instant Client (wymagany dla Oracle ≤ 11g)

### Pobieranie

Pobierz paczkę **Basic** (nie Basic Light) ze strony Oracle:
https://www.oracle.com/database/technologies/instant-client/linux-x86-64-downloads.html

Szukaj: **Version 21.x → Basic Package → ZIP**

Plik: `instantclient-basic-linux.x64-21.15.0.0.0dbru.zip`

### Instalacja

```bash
mkdir -p /opt/oracle
cd /opt/oracle
unzip instantclient-basic-linux.x64-21.15.0.0.0dbru.zip
# Wynik: /opt/oracle/instantclient_21_15/
```

### Zależności systemowe (Ubuntu 22.04 / 24.04)

```bash
# Ubuntu 22.04
apt-get install -y libaio1

# Ubuntu 24.04 (zmieniona nazwa pakietu)
apt-get install -y libaio1t64
ln -sf /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1
ldconfig
```

### LD_LIBRARY_PATH (wymagane przy każdym uruchomieniu)

```bash
export LD_LIBRARY_PATH=/opt/oracle/instantclient_21_15:$LD_LIBRARY_PATH
```

Aby ustawić na stałe:

```bash
echo '/opt/oracle/instantclient_21_15' > /etc/ld.so.conf.d/oracle-instantclient.conf
ldconfig
```

---

## 3. Konfiguracja połączenia

### Opcja A – plik INI (zalecana)

Wygeneruj przykładowy plik:

```bash
python3 oracle_diag.py --generate-config
```

Edytuj wygenerowany `oracle_diag.ini`:

```ini
[oracle]
host           = 192.168.0.66
port           = 1521
sid            = SATEST
; service_name = SATEST_SERVICE   ; alternatywa dla SID
user           = zewnuser
password       = zewnuser
instant_client = /opt/oracle/instantclient_21_15
timeout        = 10
```

Uruchomienie:

```bash
python3 oracle_diag.py --config oracle_diag.ini
```

### Opcja B – zmienne środowiskowe

```bash
export ORA_HOST=192.168.0.66
export ORA_PORT=1521
export ORA_SID=SATEST
export ORA_USER=zewnuser
export ORA_PASS=zewnuser
export ORA_CLIENT=/opt/oracle/instantclient_21_15

python3 oracle_diag.py
```

Przydatne przy CI/CD (Jenkins, Ansible) – hasła można trzymać w Vault/Secret Manager.

### Opcja C – argumenty CLI

```bash
python3 oracle_diag.py \
  --host 192.168.0.66 \
  --port 1521 \
  --sid SATEST \
  -u zewnuser \
  -p zewnuser \
  --instant-client /opt/oracle/instantclient_21_15
```

---

## 4. Użycie

```
python3 oracle_diag.py --help

opcje:
  --host IP/HOST          Adres serwera Oracle
  --port PORT             Port (domyślnie: 1521)
  --sid SID               SID bazy danych
  --service-name SERVICE  Service name (alternatywa dla SID)
  -u, --user USER         Nazwa użytkownika
  -p, --password PASS     Hasło
  --instant-client PATH   Ścieżka do Oracle Instant Client
  --timeout SECS          Timeout TCP (domyślnie: 10)
  --config FILE           Plik konfiguracyjny INI
  --no-diag               Tylko test połączenia, bez diagnostyki
  --generate-config       Wygeneruj przykładowy oracle_diag.ini
```

### Tylko test połączenia (bez pełnej diagnostyki)

```bash
python3 oracle_diag.py --config oracle_diag.ini --no-diag
```

---

## 5. Priorytet parametrów

```
CLI args  >  zmienne ENV  >  plik INI  >  wartości domyślne
```

Można np. trzymać wspólny `oracle_diag.ini` z hostem/SID, a hasło podawać przez `ORA_PASS` lub CLI.

---

## 6. Szybki start – kompletna sekwencja (Ubuntu 24.04 + Oracle 11g)

```bash
# 1. Środowisko Python
python3 -m venv /opt/venv/oracle-diag
source /opt/venv/oracle-diag/bin/activate
pip install oracledb

# 2. Instant Client
mkdir -p /opt/oracle && cd /opt/oracle
# (skopiuj zip przez scp lub wget)
unzip instantclient-basic-linux.x64-21.15.0.0.0dbru.zip

# 3. Zależności systemowe
apt-get install -y libaio1t64
ln -sf /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1
echo '/opt/oracle/instantclient_21_15' > /etc/ld.so.conf.d/oracle-instantclient.conf
ldconfig

# 4. Konfiguracja
python3 oracle_diag.py --generate-config
# edytuj oracle_diag.ini

# 5. Uruchomienie
python3 oracle_diag.py --config oracle_diag.ini
```
