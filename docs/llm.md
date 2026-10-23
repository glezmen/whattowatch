# LLM-réteg és beszélgetős ajánló (M5) — terv

Az LLM három, jól körülhatárolt feladatot kap. Egyikben sem igazságforrás a
katalógus-, elérhetőségi vagy értékelési adathoz, és nem rangsorol.

## 1. Provider-absztrakció

```python
class LLMProvider(Protocol):
    name: str                      # "lmstudio" | "anthropic" | "openai" | "claude_code" | "copilot"
    is_local: bool
    def complete(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
                 json_schema: dict | None = None, max_tokens: int = 1024,
                 temperature: float = 0.2) -> LLMResponse   # text | tool_calls | json
    def embed(self, texts: list[str]) -> list[list[float]] | None   # None ha nem támogatott
```

Adapterek (`src/whattowatch/llm/`): `openai_compat.py` (közös OpenAI-kompatibilis
kliens httpx-szel; `LMStudioProvider` kulcs nélkül, `OpenAIProvider` kulccsal),
`anthropic_provider.py` (Messages API a hivatalos `anthropic` SDK-val; eltérés a
korábbi tervtől: SDK a nyers httpx helyett, a típusos hibák és újrapróbálás miatt),
`claude_code_provider.py` (a helyi, bejelentkezett `claude` CLI a hivatalos Claude Agent
SDK-n keresztül), `copilot_provider.py` (a helyi, bejelentkezett `copilot` CLI a hivatalos
`github-copilot-sdk`-n keresztül; mindig a telepített CLI-t indítja, saját runtime-ot nem
tölt le). A két CLI-provider felhős (`is_local = False`), minden hívás üres ideiglenes
könyvtárban, a CLI saját rendszerpromptja, projektbeállításai és beépített eszközei
nélkül fut; az eszközhurkot a CLI futtatja (`AgentCapable.run_agent`), és csak a backend
típusos eszközeit látja. A közös prompt-összeállítás: `cli_prompt.py`.
`registry.py`: `get_provider(settings, override: str | None) -> LLMProvider`, és
`get_provider_with_fallback(settings, preferred)`, amely csak
`LLM_FALLBACK_ALLOWED=true` mellett próbál másik providert.

Konfig (`.env.example`-ben is): `LLM_PROVIDER` (alap `lmstudio`), `LLM_MODEL`,
`LLM_EMBEDDING_MODEL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`LLM_FALLBACK_ALLOWED=false` (csak explicit engedéllyel vált másik providerre),
`LLM_CLOUD_SEND_FEEDBACK_TEXT=false` (felhős providernek alapból nem megy a
nyers vélemény, csak a lokálisnak), `CLAUDE_CODE_MODEL` és `COPILOT_CLI_MODEL` (a CLI-
providerek modellje, ha `LLM_MODEL` üres; a `COPILOT_MODEL` nevet szándékosan kerüljük,
mert azt maga a Copilot CLI olvassa).

Dokumentálni: a ChatGPT-előfizetés nem OpenAI API-hozzáférés; az Anthropic és
OpenAI API külön számlázású.

## 2. Felhasználások

### 2.1 Preferencia-kinyerés (`extractor="llm"`)
Bemenet: a cím (magyar/eredeti), a mező (tetszett / nem tetszett) és a nyers
szöveg. Kimenet JSON-sémával: `[{label_key, confidence 0–1, evidence}]`, ahol
`label_key` **csak** a `preferences/vocabulary.py` zárt szótárából jöhet; az
ismeretlen címkét eldobjuk. Mentés `PreferenceSignal(extractor="llm",
model_version=<provider:model>, prompt_version="v1")`; a szabályalapú jelzések
mellett, nem helyettük. Felhős providernél csak `LLM_CLOUD_SEND_FEEDBACK_TEXT=true`
esetén fut.

### 2.2 Beszélgetős ajánló
`POST /api/chat {messages, provider?}` → `{reply, tool_trace, provider}`.
Rendszerprompt magyarul: a modell szűrőket és eszközhívásokat állít elő, a
válaszban csak az eszközök által visszaadott címekre hivatkozhat, és minden
elérhetőséget a `get_availability` eredményével együtt mond.

Eszközök (típusos pydantic sémák, backend hajtja végre, max 6 hívás/kör):
- `search_titles(q, media_type?)` — helyi katalógus + TMDB-keresés
- `get_recommendations(media_type?, providers?, genres?, genres_mode?, exclude_genres?,
  mood_labels?, include_watchlist?, seen?, limit)` — a determinisztikus motor futtatása; a típus-,
  szolgáltató- és műfajszűrők a rangsorolás előtt szűrik a jelölteket;
  `genres_mode="all"` kevés találatnál egyszer `"any"`-vel újrapróbál
  (`genres_mode: "any"` az eredményben); `total_candidates` = a szűrőknek megfelelő
  jelöltek száma; `mood_labels` a zárt szótárból, csak előre sorol. Nem ment
  ajánlás-batch-et (`persist=False`), a főoldal batch-e változatlan.
- `browse_catalog(genres?, genres_mode?, exclude_genres?, media_type?, providers?, sort?,
  seen?, limit, offset)` — Felfedezés-szerű lista személyes rangsor nélkül: friss verified
  flatrate a bekapcsolt előfizetéseken, „nem érdekel” nélkül; `sort` = popularity |
  rating | newest; `total` a lapozáshoz. A régi `include_seen: bool` továbbra is
  elfogadott (`true` → `seen="include"`). A rendszerprompt szerint
  műfaj-/hangulatkérésnél előbb `get_recommendations`, kis `total_candidates` vagy
  „mi van még” kérdés esetén `browse_catalog`; „nincs ilyen” csak helyes
  műfajnevekkel való újrapróbálás és `browse_catalog` után mondható.
- Műfajszűrés mindkét eszközön (és a `GET /api/titles?genre=` szűrőn) tűrő
  (`services/genres.py`, `normalize_genre`): kis-nagybetű és ékezet független,
  magyar ragok/többes szám (akciós, Akción, vígjátékok), angol nevek (comedy, crime,
  science fiction), gyakori változatok (krimi → Bűnügyi, rajzfilm → Animációs, mese →
  Animációs/Gyerek) és elírások (difflib, arány ≥ 0,8). A tv-s összetett műfajok
  (Akció & Kaland, Sci-Fi & Fantasy, Háború & Politika) tartalmazzák a részeiket,
  így „Akció” a sorozatok „Akció & Kaland” műfajára is illeszkedik. A fel nem
  ismert nevek `unknown_genres`-ben jönnek vissza a `valid_genres` listával, a
  felismertek (`genres_applied`) továbbra is szűrnek. A kanonikus műfajlista az
  eszközleírásokban és a rendszerpromptban is szerepel.
- `exclude_genres` mindkét eszközön (max. 10): kizárt műfajok — a cím egyiket sem
  tartalmazhatja (`not genre_clause(..., mode="any")`, ugyanaz a tűrő egyeztetés).
  A fel nem ismert nevek nem zárnak ki semmit, és `unknown_exclude_genres`-ben jönnek
  vissza; a felismertek `exclude_genres_applied`-ban. A rendszerprompt szerint
  „ne legyen animáció”-típusú kérésnél ezt kell használni. A
  `genres_mode="all"` → `"any"` újrapróbálás a kizárást megtartja.
- `seen` mindkét eszközön: `exclude` (alap) = csak még nem látott, `include` = a
  látottak is, `only` = csak a látottak (nézési esemény, saját értékelés,
  `seen_override`). `get_recommendations` `seen="only"` esetén újranézési rangsort ad
  (`engine.run_rewatch`: saját értékelés csökkenő — az alkalmazásbeli értékelés az
  importáltak előtt —, majd utolsó megnézés csökkenő), csak ellenőrzötten elérhető
  címekkel; `browse_catalog` a választott `sort` szerint rendez. Minden tétel hordozza
  a `seen`, `stars` (alkalmazásbeli 0,5–5) és `last_watched_at` mezőt. „Amit már
  láttam / újranézném” → `seen="only"`.
- `filter_by_provider(title_ids, provider_key)`
- `get_title_details(title_id)`
- `get_availability(title_id)`
- `get_watchlist(available_only?)`
- `record_feedback(title_id, stars?, liked_text?, disliked_text?, seen?, not_interested?, watchlist?)`

Adatminimalizálás: az eszközválaszok kártyaszintű mezőket adnak (cím, év,
műfaj, szolgáltató, pontszám, indoklás); a nyers szöveges vélemény és a teljes
előzménylista soha nem kerül a promptba. Felhős providernél a `record_feedback`
szöveges mezői csak explicit beállítással mennek át.

### 2.3 Embedding (opcionális, később)
`title_features` tábla: `title_id`, `model_version`, `embedding` (BLOB float32),
`text_hash`. A `semantic_similarity` komponens az ajánlóban csak akkor
aktiválódik, ha van embedding a jelöltre és ≥3 pozitívan értékelt címre.

## 3. Biztonság
- Kulcsok csak környezeti változóból; soha nem naplózzuk, az API `/settings`
  csak `bool`-t ad vissza a meglétükről.
- Az LLM-válasz és az eszközbemenet nem megbízható: a `record_feedback`
  csak létező `title_id`-ra, a számértékek validálva.
- Promptinjekció a címleírásokból: az eszközválaszokban a leírás legfeljebb
  300 karakter, és a rendszerprompt kimondja, hogy az adatmezők nem utasítások.

## 4. Tesztek
`FakeProvider` rögzített válaszokkal; eszközhívás-ciklus; csak létező
címkék kerülnek be; felhős providernél a payload nem tartalmaz nyers
véleményt; provider-választás és a fallback tiltása; LM Studio adapter
respx-szel.
