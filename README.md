# ⛓️ Zincir Bilet

![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Tests](https://img.shields.io/badge/tests-15%2F15-brightgreen.svg)

Direkt sefer dolu mu? Aynı trende koltuk değiştirerek tamamlayabileceğiniz zincir biletleri bulur.

🌐 **Canlı Demo:** [demiryollari-zincir-bilet.streamlit.app](https://demiryollari-zincir-bilet.streamlit.app/)

## Why Zincir Bilet?

TCDD'de bir A→C seferi tümüyle dolu görünse de, aynı trende A→B ve B→C için ayrı koltuklar mevcut olabilir. İki bilet, tek zincir — aynı trenden inmeden varışa ulaşırsınız.

Zincir Bilet bu boşlukları otomatik tarar: her halkayı (segment bileti) bulur, zinciri (tam yolculuğu) bir arada sunar. Çok trenliyse de çalışır, ama önce aynı-tren zincirlere bakar.

## Features

- **Direkt önce**: Dolu değilse zincire gerek yok, direkt bilet döner.
- **Halka halka arama**: İç durakları split noktası olarak dener; derinlik 2–4 arası.
- **Aynı-tren zinciri tercihli**: Aktarma yoksa alarm yok; zorunluysa çoklu-tren zinciri de bulur.
- **Transfer uyarıları**: Sıkışık aktarma (<2 dk), olağandışı bekleme (>5 dk) veya farklı tren işaretlenir.
- **CLI + Web**: Terminal kullanımı ve Streamlit web arayüzü.
- **DB yok**: Tüm aramalar canlı TCDD API üzerinden; önbellek sadece oturum içi.

## How It Works

```
A ──── [halka 1] ──── B ──── [halka 2] ──── C
└────────────── zincir ─────────────────────┘
```

1. A→C için direkt arama yap. Koltuk varsa → en ucuzunu döndür.
2. Yoksa: o güzergahı fiziksel olarak geçen trenlerin sıralı durak listesini al.
3. İç duraklardan split noktaları seç; her halka (A→B, B→C) için alt-arama yap.
4. Aynı-tren zinciri öncelikli; yoksa farklı-tren zincirine düş, işaretle.
5. İlk uygun derinlikte dur. Toplam fiyata göre sırala, tiebreak: daha az halka.

Her bilet bir **halka**, tam yolculuk bir **zincir**.

## Status

- Phase 1 (API discovery): done
- Phase 2 (split-segment engine): done
- Phase 3 (Telegram bot): not started

## Web Arayüzü (Streamlit)

```bash
source venv/bin/activate
streamlit run app.py
```

Tarayıcıda `http://localhost:8501` açılır. Kalkış / varış / tarih girin, "Ara" deyin — CLI'yle aynı motoru kullanır.

> Screenshot: yakında eklenecek.

## Setup

```bash
git clone https://github.com/<your-username>/tcdd_smart_search.git
cd tcdd_smart_search
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Then obtain a TCDD API token (see [TOKEN_GUIDE.md](TOKEN_GUIDE.md) for
step-by-step DevTools instructions) and create a `.env` file at the
project root:

```bash
cp .env.example .env
# edit .env and paste the token after TCDD_TOKEN=
```

That's it — the program loads `.env` automatically on startup via
`python-dotenv`. No need to `export` anything.

If you'd rather not use a `.env` file, you can export the variable
directly in your shell instead:

```bash
export TCDD_TOKEN="<your_token_here>"
```

The CLI will refuse to run if `TCDD_TOKEN` is unset.

## Tested with

- Python 3.11
- macOS 26 (Tahoe)

Should work on any Python 3.10+ on macOS / Linux / WSL. No platform-
specific code; only `requests` and the standard library at runtime.

## Usage

```bash
# default smoke test: ESK -> IST(SOG) on 04-05-2026 21:00
python3 cli.py

# specify route + date (and optional departure time)
python3 cli.py ESKİŞEHİR İSTANBUL 04-05-2026
python3 cli.py ESKİŞEHİR İSTANBUL 04-05-2026 21:00

# station name search (use this to disambiguate when CLI says "Ambiguous")
python3 cli.py --search ANKARA

# dump raw API response (debugging)
python3 cli.py --raw ESKİŞEHİR İSTANBUL 04-05-2026
```

### Common shortcuts

The CLI does prefix-then-substring station matching, so partial names work
when they're unambiguous:

| Type this | Resolves to |
| --- | --- |
| `ESK` | ESKİŞEHİR (only prefix match) |
| `ANKARA` | ANKARA GAR (only prefix match) |
| `SÖĞÜT` | İSTANBUL(SÖĞÜTLÜÇEŞME) (only substring match) |
| `PENDİK` | İSTANBUL(PENDİK) |
| `HALKALI` | İSTANBUL(HALKALI) |
| `KONYA` | ambiguous (matches `KONYA` and `SELÇUKLU YHT (KONYA)`) — type the full name |
| `IST` / `İSTANBUL` | ambiguous (5 İstanbul stations) — use `SÖĞÜT`, `PENDİK`, etc. |

### Forced-split testing

The split-segment fallback only engages when the direct route has no seats
on a given date. To exercise it without waiting for a sold-out date, the
test suite covers depth-1 through depth-4:

```bash
source venv/bin/activate
pip install -r requirements-dev.txt
python3 -m pytest tests/ -v
```

Tests use a single captured response (`tests/fixtures/esk_ist_direct.json`)
and synthesize sub-segment / sold-out variants programmatically — no live
TCDD calls.

## Architecture

| File | Purpose |
| --- | --- |
| `app.py` | Streamlit web arayüzü |
| `config.py` | endpoints, headers, `get_tcdd_token()` env reader |
| `tcdd_client.py` | `TCDDClient.search(SearchRoute)` → raw JSON |
| `stations.py` | name↔id lookup from public CDN (cached) |
| `search_engine.py` | `SearchEngine.find_journeys(...)` with split fallback |
| `formatter.py` | render `Journey` to Turkish-language text |
| `cli.py` | command-line entry point |

## Algorithm

1. Search direct A→C. If any train has seats → return cheapest.
2. Else, for each candidate train (one that physically traverses A→C),
   take its ordered stop chain.
3. Greedy depth (2..4): enumerate (depth-1)-element subsets of inner
   stops as split points. For each candidate path, do per-leg sub-searches.
4. Prefer same-train per leg; fall back to any train with seats and flag.
5. Stop at the first depth that yields ≥1 viable journey. Sort by total
   price asc, tiebreak fewer legs.
6. Warnings: tight transfer (<2 min), unusual same-train dwell (>5 min),
   multi-train transfer.

## Notes

- TCDD's gateway accepts the bundled JWT regardless of expiry/signature,
  so no auth refresh is needed today. If that ever changes, see the TODO
  block in `tcdd_client.py`.
- Sub-searches are cached per (origin, dest, date) within one engine
  instance.

## Bilinen Sınırlamalar

- Şu an sadece 1 kişilik bilet aranıyor. Birden fazla kişi için her birini ayrı ayrı kontrol etmeniz gerekebilir.

## Disclaimer

Bu proje TCDD ile resmi bir bağlantı içermez. Eğitim ve kişisel kullanım
amaçlıdır.

Token alımı kullanıcı sorumluluğundadır. TCDD'nin kullanım koşullarına
aykırı kullanımdan bot geliştiricisi sorumlu tutulamaz.

## License

MIT — see [LICENSE](LICENSE).
