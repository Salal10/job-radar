"""Location text -> countries and regions. "Remote - EMEA", "Berlin, Germany", "NYC / London", "US-CA-San Jose".

Companies write locations however they like, so this is a lookup of names, codes and big cities, not a
geocoder. Unknown places stay unknown (an empty result), they are never guessed.
"""
import re

EUROPE, MIDDLE_EAST, AFRICA = "Europe", "Middle East", "Africa"
NORTH_AMERICA, LATIN_AMERICA, ASIA, OCEANIA = "North America", "Latin America", "Asia", "Oceania"
REGIONS = [EUROPE, MIDDLE_EAST, AFRICA, NORTH_AMERICA, LATIN_AMERICA, ASIA, OCEANIA]

# country -> (region, other names / codes / big cities). Codes only where they're unambiguous in job titles.
COUNTRIES = {
    # Europe
    "United Kingdom": (EUROPE, "uk|u\\.k\\.|gb|great britain|britain|england|scotland|wales|northern ireland|london|manchester|edinburgh|glasgow|bristol|cambridge|oxford|leeds|birmingham|belfast"),
    "Ireland": (EUROPE, "dublin|cork|galway"),
    "Germany": (EUROPE, "deutschland|deutschlandweit|de|berlin|münster|bremen|essen|dortmund|mannheim|augsburg|würzburg|osnabrück|bielefeld|magdeburg|bonn|kiel|mainz|wiesbaden|freiburg|heidelberg|regensburg|göttingen|darmstadt|aachen|potsdam|rostock|erfurt|ulm|kassel|braunschweig|duisburg|bochum|wuppertal|paderborn|munich|münchen|hamburg|frankfurt|cologne|köln|stuttgart|düsseldorf|dusseldorf|leipzig|dresden|nuremberg|nürnberg|hannover|karlsruhe"),
    "Netherlands": (EUROPE, "nl|the netherlands|holland|nederland|amsterdam|rotterdam|utrecht|eindhoven|the hague|den haag|delft|leiden|groningen"),
    "France": (EUROPE, "fr|paris|lyon|marseille|toulouse|nantes|lille|bordeaux|nice|grenoble"),
    "Spain": (EUROPE, "es|españa|madrid|barcelona|valencia|seville|sevilla|malaga|málaga|bilbao"),
    "Portugal": (EUROPE, "pt|lisbon|lisboa|porto|braga|coimbra"),
    "Italy": (EUROPE, "it|italia|milan|milano|rome|roma|turin|torino|bologna|florence|naples"),
    "Belgium": (EUROPE, "be|brussels|bruxelles|antwerp|ghent|leuven"),
    "Luxembourg": (EUROPE, "lu"),
    "Switzerland": (EUROPE, "ch|schweiz|suisse|zurich|zürich|geneva|genève|basel|lausanne|bern|zug"),
    "Austria": (EUROPE, "at|österreich|vienna|wien|graz|linz|salzburg|innsbruck"),
    "Denmark": (EUROPE, "dk|copenhagen|københavn|aarhus"),
    "Sweden": (EUROPE, "se|sverige|solna|stockholm|gothenburg|göteborg|malmö|malmo|uppsala"),
    "Norway": (EUROPE, "no|norge|oslo|bergen|trondheim"),
    "Finland": (EUROPE, "fi|suomi|helsinki|espoo|tampere|oulu"),
    "Iceland": (EUROPE, "reykjavik"),
    "Poland": (EUROPE, "pl|polska|warsaw|warszawa|krakow|kraków|wroclaw|wrocław|gdansk|gdańsk|poznan|poznań|lodz|łódź"),
    "Czechia": (EUROPE, "czech republic|cz|prague|praha|brno|ostrava"),
    "Slovakia": (EUROPE, "sk|bratislava|kosice"),
    "Hungary": (EUROPE, "hu|budapest|debrecen"),
    "Romania": (EUROPE, "ro|bucharest|bucurești|cluj|cluj-napoca|iasi|iași|timisoara|timișoara"),
    "Bulgaria": (EUROPE, "bg|sofia|plovdiv|varna"),
    "Greece": (EUROPE, "gr|athens|thessaloniki"),
    "Cyprus": (EUROPE, "cy|limassol|nicosia|larnaca"),
    "Malta": (EUROPE, "mt|valletta|sliema|st julian's"),
    "Croatia": (EUROPE, "hr|zagreb|split"),
    "Slovenia": (EUROPE, "si|ljubljana"),
    "Serbia": (EUROPE, "rs|belgrade|beograd|novi sad"),
    "Bosnia and Herzegovina": (EUROPE, "bosnia|sarajevo"),
    "North Macedonia": (EUROPE, "macedonia|skopje"),
    "Albania": (EUROPE, "tirana"),
    "Montenegro": (EUROPE, "podgorica"),
    "Kosovo": (EUROPE, "pristina|prishtina"),
    "Estonia": (EUROPE, "ee|tallinn|tartu"),
    "Latvia": (EUROPE, "lv|riga"),
    "Lithuania": (EUROPE, "lt|vilnius|kaunas"),
    "Ukraine": (EUROPE, "ua|kyiv|kiev|lviv|kharkiv|odesa|dnipro"),
    "Moldova": (EUROPE, "chisinau|chișinău"),
    "Belarus": (EUROPE, "minsk"),
    "Georgia (country)": (EUROPE, "tbilisi"),  # plain "Georgia" in job ads is almost always the US state
    "Armenia": (EUROPE, "yerevan"),
    "Turkey": (EUROPE, "türkiye|turkiye|istanbul|ankara|izmir"),
    # Middle East
    "United Arab Emirates": (MIDDLE_EAST, "uae|u\\.a\\.e\\.|emirates|dubai|abu dhabi|sharjah"),
    "Saudi Arabia": (MIDDLE_EAST, "ksa|riyadh|jeddah|dammam|khobar|neom"),
    "Qatar": (MIDDLE_EAST, "doha"),
    "Bahrain": (MIDDLE_EAST, "manama"),
    "Kuwait": (MIDDLE_EAST, "kuwait city"),
    "Oman": (MIDDLE_EAST, "muscat"),
    "Israel": (MIDDLE_EAST, "il|tel aviv|tel-aviv|jerusalem|haifa|herzliya"),
    "Jordan": (MIDDLE_EAST, "amman"),
    "Lebanon": (MIDDLE_EAST, "beirut"),
    "Iraq": (MIDDLE_EAST, "baghdad|erbil"),
    # Africa
    "Egypt": (AFRICA, "eg|cairo|alexandria|giza"),
    "Morocco": (AFRICA, "casablanca|rabat|marrakech|tangier"),
    "Tunisia": (AFRICA, "tunis"),
    "Algeria": (AFRICA, "dz|algiers|oran"),
    "Nigeria": (AFRICA, "ng|lagos|abuja"),
    "Ghana": (AFRICA, "accra"),
    "Kenya": (AFRICA, "ke|nairobi|mombasa"),
    "Uganda": (AFRICA, "kampala"),
    "Rwanda": (AFRICA, "kigali"),
    "Tanzania": (AFRICA, "dar es salaam"),
    "Ethiopia": (AFRICA, "addis ababa"),
    "South Africa": (AFRICA, "za|rsa|johannesburg|cape town|durban|pretoria"),
    "Senegal": (AFRICA, "dakar"),
    "Côte d'Ivoire": (AFRICA, "ivory coast|cote d'ivoire|abidjan"),
    "Mauritius": (AFRICA, "port louis"),
    "Angola": (AFRICA, "luanda"),
    # North America
    "United States": (NORTH_AMERICA, "us|u\\.s\\.|usa|u\\.s\\.a\\.|united states of america|america|new york|nyc|san francisco|sf|bay area|los angeles|seattle|boston|chicago|austin|denver|atlanta|miami|dallas|houston|washington|dc|washington dc|philadelphia|pittsburgh|portland|san diego|san jose|palo alto|mountain view|menlo park|sunnyvale|cupertino|redwood city|brooklyn|salt lake city|phoenix|minneapolis|detroit|nashville|raleigh|charlotte|columbus|indianapolis|st\\. louis|kansas city|las vegas|orlando|tampa|baltimore|cincinnati|cleveland|irvine|santa monica|boulder|cambridge, ma|"
                      "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|georgia|tennessee|texas|utah|vermont|virginia|west virginia|wisconsin|wyoming"),
    "Canada": (NORTH_AMERICA, "toronto|vancouver|montreal|montréal|ottawa|calgary|edmonton|waterloo|quebec|québec|ontario|british columbia|alberta|manitoba|winnipeg|halifax|nova scotia"),
    # Latin America
    "Mexico": (LATIN_AMERICA, "méxico|mx|mexico city|cdmx|guadalajara|monterrey"),
    "Brazil": (LATIN_AMERICA, "brasil|br|são paulo|sao paulo|rio de janeiro|belo horizonte|curitiba|porto alegre|florianópolis|florianopolis|recife"),
    "Argentina": (LATIN_AMERICA, "ar|buenos aires|córdoba|cordoba|rosario|mendoza"),
    "Chile": (LATIN_AMERICA, "cl|santiago"),
    "Colombia": (LATIN_AMERICA, "co|bogotá|bogota|medellín|medellin|cali|barranquilla"),
    "Peru": (LATIN_AMERICA, "perú|pe|lima"),
    "Uruguay": (LATIN_AMERICA, "uy|montevideo"),
    "Paraguay": (LATIN_AMERICA, "asunción|asuncion"),
    "Ecuador": (LATIN_AMERICA, "quito|guayaquil"),
    "Bolivia": (LATIN_AMERICA, "la paz|santa cruz de la sierra"),
    "Venezuela": (LATIN_AMERICA, "caracas"),
    "Costa Rica": (LATIN_AMERICA, "cr|san josé, costa rica|san jose, costa rica"),
    "Panama": (LATIN_AMERICA, "panamá|panama city"),
    "Guatemala": (LATIN_AMERICA, "guatemala city"),
    "Dominican Republic": (LATIN_AMERICA, "santo domingo"),
    "Puerto Rico": (LATIN_AMERICA, "san juan"),
    "Jamaica": (LATIN_AMERICA, "montego bay"),
    "Honduras": (LATIN_AMERICA, "tegucigalpa|san pedro sula"),
    "Bermuda": (LATIN_AMERICA, "hamilton, bermuda"),
    # Asia
    "Pakistan": (ASIA, "pk|lahore|karachi|islamabad|rawalpindi|faisalabad|peshawar|multan"),
    "India": (ASIA, "in|bangalore|bengaluru|mumbai|delhi|new delhi|ncr|gurgaon|gurugram|noida|hyderabad|chennai|pune|kolkata|ahmedabad|kochi|jaipur|chandigarh|coimbatore|trivandrum|thiruvananthapuram|indore"),
    "Bangladesh": (ASIA, "bd|dhaka|chittagong"),
    "Sri Lanka": (ASIA, "lk|colombo"),
    "Nepal": (ASIA, "kathmandu"),
    "China": (ASIA, "cn|beijing|shanghai|shenzhen|guangzhou|hangzhou|chengdu|suzhou|nanjing|wuhan"),
    "Hong Kong": (ASIA, "hk|hong kong sar"),
    "Taiwan": (ASIA, "tw|taipei|hsinchu"),
    "Japan": (ASIA, "jp|tokyo|osaka|kyoto|yokohama|fukuoka|nagoya"),
    "South Korea": (ASIA, "korea|republic of korea|kr|seoul|busan|pangyo"),
    "Singapore": (ASIA, "sg"),
    "Malaysia": (ASIA, "my|kuala lumpur|penang|cyberjaya|johor"),
    "Indonesia": (ASIA, "id|jakarta|bandung|surabaya|bali"),
    "Philippines": (ASIA, "ph|manila|metro manila|makati|taguig|cebu|quezon city|pasig"),
    "Thailand": (ASIA, "th|bangkok|chiang mai"),
    "Vietnam": (ASIA, "viet nam|vn|ho chi minh|ho chi minh city|hanoi|ha noi|da nang"),
    "Kazakhstan": (ASIA, "almaty|astana"),
    "Uzbekistan": (ASIA, "tashkent"),
    # Oceania
    "Australia": (OCEANIA, "au|sydney|melbourne|brisbane|perth|adelaide|canberra|new south wales|nsw|queensland"),
    "New Zealand": (OCEANIA, "nz|auckland|wellington|christchurch"),
}

# Region words companies use instead of countries.
REGION_WORDS = {
    r"emea": [EUROPE, MIDDLE_EAST, AFRICA],
    r"europe|european union|\beu\b|european economic area|\beea\b|\bdach\b|nordics?|benelux|baltics|cee|eastern europe|western europe": [EUROPE],
    r"middle east|\bmena\b|gcc|gulf": [MIDDLE_EAST],
    r"africa|\bmea\b": [AFRICA],
    r"north america|\bnamer\b|\bnoram\b": [NORTH_AMERICA],
    r"latam|latin america|south america|central america|caribbean": [LATIN_AMERICA],
    r"americas|\bamer\b": [NORTH_AMERICA, LATIN_AMERICA],
    r"apac|asia[- ]pacific|\basia\b|south asia|southeast asia|\bsea\b|\banz\b": [ASIA],
    r"oceania|\banz\b": [OCEANIA],
}
ANYWHERE = re.compile(r"(?i)\b(anywhere|worldwide|world wide|global(ly)?|international|any location|all locations|earth)\b")
REMOTE = re.compile(r"(?i)remote|work from home|wfh|home[- ]?based|telecommut|distributed|anywhere|"
                    r"werken op afstand|télétravail|teletrabajo|remoto|homeoffice|home office|fernarbeit")

_SPLIT = re.compile(r"\s*(?:;|/|\||\band\b|•|\n|\+|·)\s*")  # "NYC / London", "Berlin; Remote"
# Every name as plain lowercase text -> its country. "Georgia (country)" is a display name, not a search name.
_BY_NAME = {alias.replace("\\", "").strip(): country for country, (_, aliases) in COUNTRIES.items()
            for alias in [re.sub(r" \(.*\)", "", country).lower(), *aliases.split("|")]}
_BY_NAME.pop("georgia", None) and _BY_NAME.update(georgia="United States")  # the US state, see above
_SHORT = {name for name in _BY_NAME if len(name) <= 3}  # "us", "ca", "in", "sf": only as a whole, in capitals
# Longest names first, so "new mexico" wins over "mexico" and "cambridge, ma" over "cambridge".
_PATTERN = re.compile(r"(?i)(?<!\w)(" + "|".join(re.escape(name) for name in sorted(_BY_NAME, key=len, reverse=True)
                                                 if name not in _SHORT) + r")(?!\w)")


# "Santa Clara, CA", "Austin, TX": a city then a US state code. "CA" here is California, not Canada, and
# "IN"/"DE"/"CO" are Indiana/Delaware/Colorado, not India/Germany/Colombia.
US_STATE_AFTER_CITY = re.compile(
    r"(?<!remote)(?<!Remote)(?<!REMOTE),\s*(AL|AK|AZ|AR|CA|CO|CT|DC|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|"
    r"MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)(?![A-Za-z])")


def countries_in(location: str) -> set[str]:
    """Countries named in a location string. Short codes (US, IN, DE) only count as a whole part
    ("US - Remote", "Remote, DE"), never inside text: "in" and "it" are English words."""
    # .get: some letters change length when lowercased ("İstanbul" -> "i̇stanbul"); skip those, don't crash
    found = {_BY_NAME.get(m.lower()) for m in _PATTERN.findall(location)} - {None}
    if US_STATE_AFTER_CITY.search(location) and found <= {"United States"}:  # "Bangalore, IN" is India
        found.add("United States")
        location = US_STATE_AFTER_CITY.sub(",", location)  # so the state code isn't read as a country too
    for part in re.split(r"[\s,()\-–—:/;|]+", location):
        if part.lower() in _SHORT and (part.isupper() or len(part) == 3):  # "IN" yes, "in" no; "usa" any case
            found.add(_BY_NAME[part.lower()])
    return found


def regions_in(location: str) -> set[str]:
    regions = {COUNTRIES[c][0] for c in countries_in(location)}
    for pattern, names in REGION_WORDS.items():
        if re.search(rf"(?i){pattern}", location):
            regions.update(names)
    return regions


def is_anywhere(text: str) -> bool:
    return bool(ANYWHERE.search(text))


def is_remote(text: str) -> bool:
    return bool(REMOTE.search(text))
