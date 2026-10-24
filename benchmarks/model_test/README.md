# FR-8: Worker model benchmark'leri

Bu rehber branch'in [ana README dosyasında](../../README.md) da bulunur.

Her worker bu dosyaları kendi laptopunda çalıştırır. Testler yerel Ollama'ya bağlanır.
Model adı veya kişisel IP kaynak koduna yazılmaz; seçim menüsü kurulu modellerden oluşur.

## Windows: çalıştırma

Python 3.10 veya üstü ve Ollama kurulu olmalıdır. Ollama'yı açın ve kontrol edin:

```powershell
ollama list
```

Model yoksa ekibin belirlediği modeli `ollama pull MODEL_ADI` ile kurun.
Proje klasöründen ilk çalıştırma:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\benchmarks\model_test\run_benchmarks.ps1 -InstallDependencies
```

Sonraki çalıştırmalar:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\benchmarks\model_test\run_benchmarks.ps1
```

`-InstallDependencies`, aynı Python için `requests` paketini kurar. Execution policy yalnızca
bu işlem için geçerlidir; kalıcı ayar değiştirilmez. Scriptlere izin verilen sistemlerde
`.\benchmarks\model_test\run_benchmarks.ps1` doğrudan kullanılabilir. Başka bir klasördeyseniz
başlatıcı yolunu bulunduğunuz klasöre göre verin.

## Seçim menüsü

1. Model listesinden bir numara girin. Aynı model tüm seçilen testlerde kullanılır.
2. Test seçin: `1` Coding, `2` Reasoning, `3` Context, `4` Speed, `0` Hepsi.
   Tek test için `2`, birkaç test için `1,2` veya `2,3,4` yazın.
   Tekrarlar birleştirilir; geçersiz seçim tekrar sorulur. `0` tek başına kullanılmalıdır.
3. Mod seçin: `1` Quick (varsayılan) veya `2` Full. Speed tek başına seçilirse mod sorulmaz.
4. Testler sırayla çalışır. İstek hatası bir sonraki seçilen testi engellemez.
5. Son özette durum ve JSON yolları gösterilir. `completed`, bütün soruların doğru
   cevaplandığı anlamına gelmez; puanı JSON'dan okuyun.

## Kapsam ve sürümler

| Test dosyası | Sürüm | Quick | Full / standart |
| --- | --- | --- | --- |
| `model_coding_test.py` | `coding-v3` | 10 görev × 5 örnek | 20 görev × 5 örnek |
| `model_reasoning_test.py` | `reasoning-v4` | 8 soru | 12 soru |
| `model_context_test.py` | `context-retrieval-v2` | 3 metin | 6 metin |
| `model_speed_test.py` | `speed-v2` | Aynı ölçüm | 1 ısınma + 3 ölçüm |

Coding dağılımı: 4 easy, 6 medium, 6 hard, 4 expert. Ağırlıklar %10, %25, %35, %30.
Her seviyedeki doğru örnek oranı hesaplanır; ağırlıklı toplam en sonda yuvarlanır.
Testler doğruluğu ölçer; algoritmik karmaşıklık hedeflerini ayrıca ölçmez.
Reasoning dağılımı: 3 easy, 4 medium, 5 hard; sorular eşit ağırlıklıdır.
Context'te 350, 700, 1200, 2000, 3500 ve 5000 dolgu kelimelik seviyeler korunur.
Bunlar tokenizer token sayısı veya maksimum context kapasitesi değildir.

Revizyondaki değişiklikler:

- Coding M2: işletim sistemine bağlı POSIX yol sorusu yerine aynı seviyede,
  stack gerektiren ardışık karakter gruplarını silme sorusu geldi.
- Coding X1: liste/tuple dönüş farkı yerine atomik işlem gruplarını geri alma
  sorusu geldi; yalnızca son bakiyeler sözlüğü döndürülür.
- Coding X2: hatalı işlemde `ValueError` beklentisi gerçekten sınanır. Başarılı
  ve hatalı işlemlerde orijinal girdiyi değiştirmeme koşulu kontrol edilir.
- Coding X4: bozuk kayıtlar ve bool/int ayrımı netleştirildi; kesirli ortalamada
  1e-9 kayan nokta toleransı uygulanır. E4'te girdiyi değiştirmeme kontrolü eklendi.
- Reasoning R01: belirsiz sayı dizisi yerine açık tanımlı yineleme geldi.
  R03'te zaman varsayımı, R05'te tam katılımcı listesi açıklandı. Prompt'tan sorularla
  aynı cevapları içeren örnekler kaldırıldı. Çelişkili birden fazla final cevap reddedilir.
- Context: her metne üç farklı kayıt için dikkat dağıtıcı kodlar eklendi;
  hedef kayıt tektir. Tam kod eşitliği gerekir; eksik karakter doğru sayılmaz.
- Speed: bir ısınma ve üç farklı sabit metin istemi kullanılır. Her ölçüm en az
  32 çıktı token'ı ve pozitif `eval_duration` gerektirir.

Gerçek yanlış cevaplar nedeniyle sağlam sorular kaldırılmadı. Bu revizyon istatistiksel
bir zorluk kalibrasyonu değildir; seviyeler ve soru sayıları korunmuştur. Yeni skorları
eski sürümlerle doğrudan karşılaştırmayın; modelleri aynı yeni sürüm ve modla tekrar ölçün.

## JSON sonuçları

Varsayılan konum, çalışma klasöründen bağımsız olarak proje kökündeki `benchmark/`:

```text
benchmark/
  qwen2.5-coder_14b/
    model_coding_test_full_20261024T120000123456Z.json
    model_reasoning_test_full_20261024T121000123456Z.json
    model_context_test_full_20261024T122000123456Z.json
    model_speed_test_standard_20261024T123000123456Z.json
```

Tarih/saat UTC'dir; mikrosaniyeler ayrı çalıştırmaları ayırır. Önceki JSON'lar değiştirilmez.
Model adındaki `:` veya `/`, klasör adı için `_` olur. JSON'da sürüm, model, mod,
generation seçenekleri, seed, işletim sistemi/Python bilgisi ve görev ayrıntıları bulunur.
Coding'de gerçek/beklenen çıktı, exception ve girdi değişimi bilgisi de saklanır.
`started_at`, ilk sonuç kaydının zamanıdır; `updated_at` son güncellemedir.

- `status: completed`, `score_valid: true`: test bitti; yanlış cevaplar puana yansır.
- `status: error`, `score_valid: false`: bağlantı, bozuk API yanıtı veya geçersiz hız
  örneği gibi ölçüm sorunu vardır. Nihai puan `null` olur. Bu kaydı agent yetenek
  puanı olarak kullanmayın. Başarılı alt görevler dosyada kalır.
- Coding çözümünün çalışmaması veya timeout olması model hatasıdır; örnekler
  başarısız sayılır. Bu, altyapı hatasıyla karıştırılmaz.
- Ctrl+C sırasında sonuç dosyası varsa `interrupted` ve `score_valid: false`
  kaydedilir. Kalan testler başlatılmaz.
- Her görev/ölçüm sonrası atomik kayıt yapılır. Ani süreç kapanmasında son kayıt
  `running` kalabilir. Otomatik devam etme yoktur.

Speed, ham `tokens_per_second` ve medyanı saklar; ısınma puana katılmaz. Üç ölçümün
biri bile geçersizse nihai hız puanı verilmez. Politika: `min(100, 100 × median_tok_s / 20)`.
20 token/sn hedefi proje kararıdır. 100 alan modelleri ham medyanla karşılaştırın.
Toplam istek süresi ayrı saklanır; üretim hızı yükleme ve ağ süresini içermez.

## İsteğe bağlı kullanım

Python ile aynı menü:

```powershell
python -m pip install -r benchmarks/model_test/requirements.txt
python benchmarks/model_test/run_benchmarks.py
```

Adres ve çıktı konumu:

```powershell
.\benchmarks\model_test\run_benchmarks.ps1 -Url http://localhost:11434 -ResultsDir benchmark
```

Varsayılan adres `OLLAMA_DEFAULT_URL` ortam değişkeninden, yoksa `http://localhost:11434`
değerinden gelir. Kendi modeliniz için Tailscale IP'si gerekmez. Başka bir Ollama için
MagicDNS adı kullanılabilir; coding kodu scripti çalıştıran bilgisayarda yürütülür.

Tek tek çalıştırma, proje kökünden:

```powershell
python benchmarks/model_test/model_coding_test.py --mode quick
python benchmarks/model_test/model_reasoning_test.py --mode full
python benchmarks/model_test/model_context_test.py --mode quick
python benchmarks/model_test/model_speed_test.py --repeats 3 --target-tps 20
```

Her test dosyası `--model KURULU_MODEL_ADI`, `--all`, `--url`, `--results-dir` destekler.
`--model` verilmezse model menüsü açılır.

## Sorun giderme ve doğrulama

- Bağlantı reddediliyor: Ollama'yı açın, `ollama list` ile kontrol edin.
- Model listesi boş: önce ekibin seçtiği modeli indirin.
- `requests` bulunamıyor: başlatıcıyı `-InstallDependencies` ile tekrar çalıştırın.
- Timeout/bellek sorunu: diğer model üretimlerini durdurun, quick modla başlayın.
- Sonuç `error`: JSON'daki ayrıntılara bakın; bu bir sıfır yetenek puanı değildir.

Gerçek model çalıştırmadan otomatik kontroller:

```powershell
python -m unittest discover -s benchmarks/model_test -p "test_*.py" -v
```

Coding, üretilen Python'u ayrı süreçte yürütür; güvenlik sandbox'ı değildir. İzole ortam
kullanın. Aynı sürüm, mod ve üretim ayarlarını kullanarak karşılaştırın. Donanım ve arka plan
yükü özellikle hızı etkiler; sabit seed farklı donanımda aynı yanıtı garanti etmez.

Bu paket master'a otomatik sonuç gönderimi veya Django/web entegrasyonu içermez.
