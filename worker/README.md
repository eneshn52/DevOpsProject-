# FR 16 Main Üzerindeki Eksik Yürütme Akışının Tamamlanması

Kaynak: verilen GitHub deposunun main dalı, commit `5e30ee0ac6a10483d61cd3548abb60d77c28abc3`.

## Main'de hazır olan ve korunan kısım

- `django_modules/task_distribution.py`: FR-14 ile görev seçip worker'a veren mevcut GET next-task endpoint'i. Dosya değiştirilmedi.
- `django_modules/agent_heartbeat.py`: mevcut heartbeat ve online/offline davranışı. Dosya değiştirilmedi.
- `worker/worker.py`: mevcut worker, config yükleme, HTTP istemcisi ve heartbeat-only çalışma akışı. Bunlar korunarak `--execute` seçeneği eklendi.
- FR-8 profilleri, scheduler, task planning ve requirements dosyaları değiştirilmedi.

Yeni bir alternatif uygulama veya ayrı settings sistemi kurulmadı. Mevcut worker ve mevcut next-task URL'si kullanılır.

## Eksik kalan ve eklenen kısım

```text
Mevcut next-task endpoint'inden görevi al
→ O görev için yürütme kaydı oluştur
→ Worker'ın yerel Ollama modelinde çalıştır
→ Cevabı/hata durumunu HTTP ile master'a gönder
→ Görev durumunu ve sonucu kaydet
```

Eksik model çağrısı ve sonuç teslimi `worker/task_runner.py` dosyasındadır. Mevcut worker komutu bu dosyayı `--execute` ile çağırır. `django_modules/task_execution.py` görevin başlangıç ve sonuç endpoint'lerini ekler. Model yanıtı yeni WorkerTaskResult kaydında saklanır; mevcut Task, Agent ve Metric modelleri kullanılır.

Main'de bulunmayan başlangıç/sonuç/profile URL'leri mevcut urls.py'ye eklendi. Mevcut heartbeat ve next-task URL'leri, konfigüre edilmiş FR-16 agent'ı için token kontrolüyle aynı özgün fonksiyonlara gider. Bu fonksiyonların atama ve heartbeat mantığı değiştirilmedi. Token konfigüre edilmemiş eski heartbeat-only worker'lar mevcut erişim kurallarıyla çalışmayı sürdürür.

## Belgedeki FR 16 ile uyum

FR-16'nın üç adımı HTTP ile görev alma, yerel modelde yürütme ve sonucu gönderme olarak uygulanır. İlgili yürütme şartları da bu akışın içinde ele alınır:

- NFR-03: UTF-8 byte üst sınırı ve şablon payıyla muhafazakâr %80 girdi bütçesi kontrolü. Tam tokenizer entegrasyonu değildir.
- NFR-05: görev deadline'ı ve model isteği timeout'u. Varsayılan 180 saniye; FR16_TASK_TIMEOUT ile ayarlanır.
- NFR-06: worker standart Python kütüphanesini kullanır; platforma özgü shell komutu çalıştırmaz. Windows/Linux'ta ayrıca cihaz testi gerekir.
- NFR-07: kayıtlı worker tek config ve tek başlangıç komutuyla çalışır. FR-4 registration bu değişiklikte yeniden yapılmadı.
- NFR-08 ve NFR-09: görev/prompt/yanıt/hata, model adı, yerel model digest'i ve generation seçenekleri kaydedilir.
- NFR-11: agent'a özgü ortak token kullanılır. Master görev paketi HMAC ile imzalanır; worker imza yanlışsa modeli çağırmaz. Worker HTTP dinleyicisi açmaz. Özel ağ varsayımı korunur.

Bu yalnızca FR-16'nın tamamlanmasıdır. Benchmark/puanlama, Git commit, otomatik retry/reassignment, üretilen kodun çalıştırılması, proje entegrasyonu veya dashboard yeniden yazılmadı. Bunlar ilgili diğer FR'lerdir. Modelin open-weight ve donanıma uygun seçilmesi ekip sorumluluğundadır.

## Kurulum

Tam ZIP'i masaüstüne ayrı `DevOpsProject-FR16-Main` klasörü olarak çıkar. Kendi branch'ında kullanacaksan değişiklik listesine bak; önceki sohbet paketlerini ekleme.

```sh
cd ~/Desktop/DevOpsProject-FR16-Main
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
export USE_SQLITE=true
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8002
```

Özgün requirements.txt aynıdır. PostgreSQL kullanılıyorsa USE_SQLITE seçmeden mevcut DB ortam ayarlarını kullan. Windows PowerShell'de ortam `venv\Scripts\Activate.ps1` ile açılır; SQLite için `$env:USE_SQLITE="true"` kullanılır.

`http://127.0.0.1:8002/admin/` adresinde model ve agent kayıtlarını mevcut yöntemle oluştur. Model adı `ollama list` ile eşleşmeli; örneğin qwen2.5-coder:3b. Model destekliyorsa agent context window için 8192 kullan. Agent ID'yi not et.

## Ortak token

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Master terminalinde sunucuyu durdurup agent ID'si 1 ise ürettiğin token ile:

```sh
export FR16_WORKER_TOKENS='{"1":"BURAYA_URETTIGIN_TOKEN"}'
python manage.py runserver 127.0.0.1:8002
```

Farklı agent'lara farklı token ver. `.env` dosyasının otomatik yüklendiğini varsayma. Token'a göre mevcut worker endpoint'leri için erişim açılır; bu ayar mevcut registry veya admin erişimini genişletmez.

## Mevcut worker ile yürütmeyi başlatma

Ollama worker bilgisayarında açık ve seçilen model kurulu olmalı. Kontrol: `ollama list`. Gerekiyorsa `ollama serve` ve model indirme işlemini yap.

Yeni terminalde proje kökünde:

```sh
cp worker/fr16.config.example.json worker/config.json
```

Config'te agent_id ve shared_token değerlerini master ile eşleştir. server_url yerel denemede `http://127.0.0.1:8002`, ollama_url worker'ın yerel `http://127.0.0.1:11434` adresidir. `model` alanını boş bırak; master'daki atanan model snapshot'ı kullanılır. Token istenirse dosya yerine FR16_WORKER_TOKEN ortam değişkeniyle sağlanabilir.

```sh
python3 worker/worker.py --config worker/config.json --execute
```

Bu aynı ana worker komutudur; sadece yürütme seçeneği eklendi. Eski heartbeat-only kullanım `--execute` olmadan korunur. Aynı agent için iki worker süreci çalıştırma. Model yanıtı beklenirken heartbeat otomatik sürer.

Tek görev denemesi için `--once` ekle. Durdurmak için Control+C. `--sign-off` mevcut seçeneğiyle worker dururken offline bildirimi gönderilebilir.

## İlk görev

Admin > Tasks > Add:

- Title: Addition function
- Instructions: Write a Python function add(a, b) that returns their sum. Return only code.
- Assigned agent: oluşturduğun worker
- Status: ASSIGNED
- Max tokens: 512

Bu manuel atama, FR-16'yı tek başına denemek içindir. PENDING görevler ana projedeki mevcut FR-14 seçimine girer; ilgili FR-8 puanlarının ve diğer uygunluk koşullarının sağlanması gerekir.

Beklenen durumlar: ASSIGNED → IN_PROGRESS → COMPLETED veya FAILED. Sonuç Admin > Worker task results bölümündedir. COMPLETED yalnızca model cevabının master'a kaydedildiğini belirtir; üretilen kod için doğruluk testi anlamına gelmez.

## API ve teknik sınırlar

Korunan URL'ler:

- GET `/api/agents/<id>/next-task/`
- POST `/api/agents/<id>/heartbeat/`

Eksik parçayı tamamlamak için eklenen URL'ler:

- GET `/api/agents/<id>/execution-profile/`
- POST `/api/agents/<id>/tasks/<task_id>/start/`
- POST `/api/agents/<id>/tasks/<task_id>/result/`

Yeni migration `django_modules/0008_workertaskresult.py`. Branch'ında başka bir 0008 migration varsa birleştirilmesi gerekir; başka bir migration'ı silme veya üzerine yazma.

Ağ kesilince model sonucu config yanında `.fr16-outbox.json` dosyasında bekler; yeniden başlatmada önce o gönderilir. Tekrarlanan aynı sonuç ikinci Metric üretmez. Eski attempt/lease veya farklı final sonuç reddedilir. Deadline worker isteklerinde kontrol edilir; ayrı arka plan tarayıcısı eklenmedi. Yerel Ollama'nın sunucu tarafı üretimini iptal eden bir çağrı yoktur. Model çağrısı sürerken süreç kapanırsa yeniden üretim yapılabilir; exactly-once inference garantisi yoktur.

Farklı bilgisayarda master'ın özel ağ adresini server_url ve mevcut deployment ALLOWED_HOSTS yapılandırmasında kullan. Ortak ağ/same Wi-Fi veya Tailscale varsayımı belgeyle aynıdır. Public internete yayınlama kapsam dışıdır.

## Testler

```sh
python -m pip install -r requirements-dev.txt
USE_SQLITE=true python manage.py test
USE_SQLITE=true python -m pytest django_modules/test_scheduler.py -q
```

FR-16 testleri token, görev imzası, görev sahipliği, timeout, context sınırı, model digest kaydı, idempotent teslim, outbox ve gerçek HTTP üzerinden mevcut next-task/heartbeat işlevleriyle sahte Ollama iletişimini kapsar. Gerçek model ve PostgreSQL kilit davranışı bu yeni çalıştırmada ayrıca doğrulanmadı.
