"""Closed preference-label vocabulary (docs/recommender.md section 4).

Keywords are lowercase Hungarian stems (diacritics kept) matched as substrings of the
casefolded text, so a stem also covers its suffixed forms. Stems are chosen so that a
common opposite form does not contain them (e.g. "kiszámítható" vs "kiszámíthatatlan").

`tmdb_genres` use TMDB Hungarian genre names (`/genre/{movie,tv}/list?language=hu-HU`);
`tmdb_keywords` use TMDB keyword names (English).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Label:
    category: str
    key: str
    name_hu: str
    keywords_hu: tuple[str, ...]
    tmdb_genres: tuple[str, ...] = ()
    tmdb_keywords: tuple[str, ...] = ()

    @property
    def full_key(self) -> str:
        return f"{self.category}/{self.key}"


_L = Label

_LABELS: tuple[Label, ...] = (
    # --- tempo ---
    _L("tempo", "lassu", "lassú tempó",
       ("lassú", "vontatott", "ráérős", "lassan halad", "elnyújtott", "lomha")),
    _L("tempo", "gyors", "gyors tempó",
       ("gyors", "pörgős", "dinamikus", "lendületes", "feszes tempó"),
       ("Akció", "Akció & Kaland")),
    _L("tempo", "hosszu", "hosszú, terjengős",
       ("hosszú", "terjengős", "túlnyújtott", "felesleges jelenet", "töltelék")),
    # --- hangulat ---
    _L("hangulat", "sotet", "sötét hangulat",
       ("sötét", "nyomasztó", "komor", "borongós", "kilátástalan", "baljós"),
       ("Thriller", "Horror", "Bűnügyi"), ("neo-noir", "dark")),
    _L("hangulat", "konnyed", "könnyed hangulat",
       ("könnyed", "laza", "szórakoztató", "kikapcsol", "felhőtlen", "habkönnyű"),
       ("Vígjáték", "Családi")),
    _L("hangulat", "feszult", "feszült hangulat",
       ("feszült", "feszültség", "izgalmas", "idegtépő", "pattanásig", "végig izgul"),
       ("Thriller", "Rejtély"), ("suspense",)),
    _L("hangulat", "melankolikus", "melankolikus hangulat",
       ("melankolikus", "melankólia", "szomorú", "elégikus", "keserédes", "bánatos"),
       ("Dráma",)),
    _L("hangulat", "meleg", "meleg, szívmelengető",
       ("szívmelengető", "megható", "bájos", "kedves", "meleg hangulat", "aranyos"),
       ("Családi",)),
    _L("hangulat", "romantikus", "romantikus",
       ("romantikus", "romantika", "szerelmes", "szerelmi", "szerelem"),
       ("Romantikus",), ("romance",)),
    _L("hangulat", "felemelo", "felemelő, optimista",
       ("felemelő", "inspiráló", "optimista", "reményt ad", "lélekemelő")),
    _L("hangulat", "atmoszferikus", "erős atmoszféra",
       ("atmoszféra", "atmoszferikus", "hangulatos", "hangulatvilág", "miliő")),
    # --- humor ---
    _L("humor", "vicces", "humoros",
       ("vicces", "humor", "nevettem", "nevettet", "nevetés", "röhög", "mulatságos", "poén"),
       ("Vígjáték",), ("comedy",)),
    _L("humor", "szaraz", "száraz humor",
       ("száraz humor", "szarkaz", "ironikus", "irónia", "angol humor", "humora száraz"),
       ("Vígjáték",), ("satire",)),
    _L("humor", "abszurd", "abszurd humor",
       ("abszurd", "groteszk", "bizarr", "szürreál", "agyament"),
       ("Vígjáték",), ("absurdism", "surreal")),
    _L("humor", "szituacios", "szituációs humor",
       ("szituációs", "helyzetkomikum", "félreértés", "sitcom"),
       ("Vígjáték",), ("sitcom",)),
    _L("humor", "fekete", "fekete humor",
       ("fekete humor", "sötét humor", "morbid", "cinikus humor", "akasztófahumor"),
       ("Vígjáték",), ("dark comedy", "black comedy")),
    # --- tortenet ---
    _L("tortenet", "csavaros", "csavaros, kiszámíthatatlan",
       ("csavaros", "csavar", "fordulat", "kiszámíthatatlan", "meglepetés", "váratlan"),
       ("Rejtély", "Thriller"), ("plot twist",)),
    _L("tortenet", "kiszamithato", "kiszámítható, sablonos",
       ("kiszámítható", "előre látható", "sablonos", "klisé", "elcsépelt", "közhelyes")),
    _L("tortenet", "lassan_kibontakozo", "lassan kibontakozó történet",
       ("lassan kibontakozó", "lassan bontakozik", "lassan épít", "lassú építkezés",
        "fokozatosan", "slow burn"),
       (), ("slow burn",)),
    _L("tortenet", "tobb_szalu", "több szálon futó történet",
       ("több szál", "párhuzamos történet", "szálak", "összefonódó", "mozaikos"),
       (), ("multiple storylines",)),
    _L("tortenet", "lezaratlan", "lezáratlan befejezés",
       ("lezáratlan", "nyitott vég", "nyitva hagy", "függővég", "cliffhanger",
        "nincs lezárva"),
       (), ("open ending", "cliffhanger")),
    _L("tortenet", "rejtely", "rejtély, nyomozás",
       ("rejtély", "nyomoz", "whodunit", "titokzatos", "talány"),
       ("Rejtély", "Bűnügyi"), ("mystery", "whodunit", "investigation")),
    _L("tortenet", "okos", "okos, átgondolt történet",
       ("okos", "intelligens", "átgondolt", "kidolgozott történet", "jól megírt",
        "forgatókönyv")),
    _L("tortenet", "logikai_hiba", "logikai hibák",
       ("logikai hiba", "logikátlan", "következetlen", "lyukas", "nincs értelme",
        "erőltetett")),
    # --- karakter ---
    _L("karakter", "antihos", "antihős",
       ("antihős", "morálisan szürke", "szürke zóna", "gonosz főhős", "negatív főhős"),
       (), ("antihero",)),
    _L("karakter", "eros_noi", "erős női főszereplő",
       ("erős női", "női főszereplő", "női főhős", "erős nő", "karakán nő"),
       (), ("female protagonist", "strong woman")),
    _L("karakter", "ensemble", "ensemble szereplőgárda",
       ("ensemble", "sok szereplő", "szereplőgárda", "társulat", "színes karakterek"),
       (), ("ensemble cast",)),
    _L("karakter", "irritalo", "irritáló szereplő",
       ("irritáló", "idegesítő", "unszimpatikus", "ellenszenves", "bosszantó")),
    _L("karakter", "fejlodes", "karakterfejlődés, mély karakterek",
       ("karakterfejlődés", "jellemfejlődés", "fejlődik", "összetett karakter",
        "mély karakter", "kidolgozott karakter")),
    _L("karakter", "alakitas", "színészi játék",
       ("színészi", "alakítás", "színész", "szereposztás", "játéka")),
    _L("karakter", "gonosz", "emlékezetes gonosz",
       ("főgonosz", "gonosz", "ellenlábas", "villain"),
       (), ("villain",)),
    _L("karakter", "kemia", "szereplők közti kémia",
       ("kémia", "párosa", "a páros", "duó", "összhang")),
    # --- stilus ---
    _L("stilus", "eroszakos", "erőszakos",
       ("erőszak", "brutális", "kegyetlen", "durva"),
       (), ("violence",)),
    _L("stilus", "veres", "véres",
       ("véres", "vérontás", "gore", "splatter", "belez"),
       ("Horror",), ("gore", "splatter")),
    _L("stilus", "latvanyos", "látványos",
       ("látvány", "vizuális", "operatőr", "effekt", "gyönyörű kép", "képi világ"),
       ("Sci-Fi", "Fantasy", "Kaland", "Sci-Fi & Fantasy")),
    _L("stilus", "dokumentarista", "dokumentarista stílus",
       ("dokumentarista", "dokumentumfilm", "dokumentum", "kézikamer", "riport"),
       ("Dokumentumfilm",), ("documentary", "mockumentary")),
    _L("stilus", "feliratos", "feliratos, eredeti nyelvű",
       ("felirat", "eredeti nyelv", "idegen nyelv", "nem angol nyelvű")),
    _L("stilus", "animacio", "animáció",
       ("animáció", "animációs", "rajzfilm", "anime"),
       ("Animációs",), ("anime",)),
    _L("stilus", "zene", "zene, filmzene",
       ("zene", "zenei", "soundtrack", "betétdal", "musical"),
       ("Zene",), ("musical",)),
    _L("stilus", "parbeszed", "párbeszédek",
       ("párbeszéd", "dialóg", "beszólás", "szövegelés"),
       ("Talk",)),
    _L("stilus", "akcio", "akciójelenetek",
       ("akció", "harc", "lövöldöz", "üldöz", "verekedés", "kaszkadőr"),
       ("Akció", "Akció & Kaland"), ("martial arts",)),
    # --- tema ---
    _L("tema", "bunugy", "bűnügy",
       ("bűnügy", "bűnöz", "maffia", "rendőr", "gyilkos", "krimi", "drog"),
       ("Bűnügyi",), ("crime", "mafia", "serial killer", "police")),
    _L("tema", "politika", "politika",
       ("politik", "hatalmi harc", "választás", "kormány", "korrupció"),
       ("Háború & Politika",), ("politics", "corruption")),
    _L("tema", "csalad", "család",
       ("család", "szülő", "testvér", "apa és", "anya és"),
       ("Családi",), ("family relationships",)),
    _L("tema", "tulles", "túlélés",
       ("túlél", "apokalip", "katasztrófa", "vadon", "zombi"),
       (), ("survival", "post-apocalyptic future", "zombie")),
    _L("tema", "tudomany", "tudomány, sci-fi",
       ("tudomány", "tudós", "sci-fi", "scifi", "világűr", "űrhajó", "technológi", "jövőben"),
       ("Sci-Fi", "Sci-Fi & Fantasy"), ("science", "space", "artificial intelligence")),
    _L("tema", "fantasy", "fantasy, mágia",
       ("fantasy", "varázs", "mágia", "mágikus", "sárkány", "tündér"),
       ("Fantasy", "Sci-Fi & Fantasy"), ("magic", "dragon")),
    _L("tema", "horror", "horror, félelem",
       ("horror", "ijesztő", "rémisztő", "félelmetes", "kísérte", "démon", "paranormális"),
       ("Horror",), ("supernatural", "haunted house")),
    _L("tema", "tortenelmi", "történelmi",
       ("történelmi", "korabeli", "kosztümös", "középkor", "kordráma", "korhű"),
       ("Történelmi",), ("period drama", "historical")),
    _L("tema", "haboru", "háború",
       ("háború", "katona", "csata", "fronton", "hadsereg"),
       ("Háborús", "Háború & Politika"), ("war", "world war ii")),
    _L("tema", "felnoves", "felnövés, kamaszkor",
       ("felnövés", "kamasz", "tinédzser", "iskolás", "coming of age"),
       (), ("coming of age", "high school")),
    _L("tema", "kapcsolatok", "kapcsolatok, barátság",
       ("barátság", "párkapcsolat", "házasság", "válás", "kapcsolatok"),
       ("Dráma",), ("friendship", "marriage")),
    _L("tema", "sport", "sport",
       ("sport", "foci", "bajnokság", "edző", "mérkőzés"),
       (), ("sports", "boxing", "football")),
    _L("tema", "igaz_tortenet", "igaz történet, életrajz",
       ("igaz történet", "valós esemény", "megtörtént", "életrajz", "valós történet"),
       ("Történelmi", "Dokumentumfilm"), ("based on true story", "biography")),
    _L("tema", "tarsadalom", "társadalmi kérdések",
       ("társadalm", "szegénység", "rasszizmus", "igazságtalanság", "osztálykülönbség"),
       ("Dráma",), ("social commentary", "racism", "poverty")),
)  # fmt: skip

_BY_KEY: dict[str, Label] = {label.full_key: label for label in _LABELS}


def all_labels() -> tuple[Label, ...]:
    return _LABELS


def by_key(key: str) -> Label:
    """Return the label for `"category/key"`; raises KeyError if unknown."""
    return _BY_KEY[key]


def labels_for_title(genres: Iterable[str], keywords: Iterable[str] = ()) -> frozenset[str]:
    """Label keys implied by a title's TMDB genres/keywords (case-insensitive)."""
    genre_set = {g.casefold() for g in genres}
    keyword_set = {k.casefold() for k in keywords}
    return frozenset(
        label.full_key
        for label in _LABELS
        if genre_set.intersection(g.casefold() for g in label.tmdb_genres)
        or keyword_set.intersection(k.casefold() for k in label.tmdb_keywords)
    )
