"""Sorting aircraft into a handful of recognisable kinds, for choosing an icon.

The feed gives an ICAO type designator ("B738", "C172") for most aircraft. The table below
covers the types that make up nearly all everyday traffic; anything else falls back to the
coarse ADS-B emitter category the aircraft itself broadcasts, and then to a generic arrow.
"""

from enum import StrEnum


class Kind(StrEnum):
    WIDEBODY = "widebody"
    NARROWBODY = "narrowbody"
    REGIONAL_JET = "regional_jet"
    BUSINESS_JET = "business_jet"
    TURBOPROP = "turboprop"
    PISTON_TWIN = "piston_twin"
    HIGH_WING = "high_wing"  # Cessna singles and their like
    LOW_WING = "low_wing"  # Piper singles and their like
    COMPOSITE = "composite"  # Cirrus, Diamond, Columbia
    HELICOPTER = "helicopter"
    HEAVY_HELICOPTER = "heavy_helicopter"
    UNKNOWN = "unknown"


LABELS: dict[Kind, str] = {
    Kind.WIDEBODY: "Widebody jet",
    Kind.NARROWBODY: "Airliner",
    Kind.REGIONAL_JET: "Regional jet",
    Kind.BUSINESS_JET: "Business jet",
    Kind.TURBOPROP: "Turboprop",
    Kind.PISTON_TWIN: "Light twin",
    Kind.HIGH_WING: "Light aircraft (high wing)",
    Kind.LOW_WING: "Light aircraft (low wing)",
    Kind.COMPOSITE: "Light aircraft (composite)",
    Kind.HELICOPTER: "Helicopter",
    Kind.HEAVY_HELICOPTER: "Heavy helicopter",
}

_BY_KIND: dict[Kind, str] = {
    Kind.WIDEBODY: """
        A306 A30B A310 A332 A333 A337 A338 A339 A342 A343 A345 A346 A359 A35K A388 A3ST
        B741 B742 B743 B744 B748 B74D B74R B74S B762 B763 B764 B772 B773 B778 B779 B77L B77W
        B788 B789 B78X BLCF DC10 MD11 L101 IL86 IL96 IL76 A124 A225
        C17 C5M K35R KC10 KC46 E3TF E3CF E6 B52 A400 C141 MRTT
    """,
    Kind.NARROWBODY: """
        A318 A319 A320 A321 A19N A20N A21N BCS1 BCS3
        B712 B721 B722 B731 B732 B733 B734 B735 B736 B737 B738 B739 B37M B38M B39M B3XM
        B752 B753 MD81 MD82 MD83 MD87 MD88 MD90 DC93 DC95 C919 T204 T154 P8 E737
    """,
    Kind.REGIONAL_JET: """
        CRJ1 CRJ2 CRJ7 CRJ9 CRJX E135 E145 E45X E170 E175 E75L E75S E190 E195 E290 E295
        F70 F100 F28 RJ70 RJ85 RJ1H B461 B462 B463 SU95 AR21 A148 A158
    """,
    Kind.BUSINESS_JET: """
        C500 C501 C510 C525 C25A C25B C25C C25M C550 C551 C55B C560 C56X C650 C680 C68A C700
        C750 CL30 CL35 CL60 E35L E50P E55P E545 E550 F2TH F900 FA10 FA20 FA50 FA6X FA7X FA8X
        G150 G200 G280 GALX ASTR GLF2 GLF3 GLF4 GLF5 GLF6 GA5C GA6C GA7C GA8C GLEX GL5T GL7T
        H25A H25B H25C HA4T HDJT BE40 PRM1 LJ24 LJ25 LJ31 LJ35 LJ40 LJ45 LJ55 LJ60 LJ70 LJ75
        PC24 SF50 EA50 MU30 WW24
    """,
    Kind.TURBOPROP: """
        DH8A DH8B DH8C DH8D AT43 AT44 AT45 AT46 AT72 AT73 AT75 AT76 SF34 SB20 B190 E110 E120
        JS31 JS32 JS41 D228 D328 F27 F50 SW3 SW4 BE99 BE9L BE9T BE10 BE20 BE30 B350 B36T
        C208 C212 C295 CN35 DHC6 DHC7 PC12 PC6T TBM7 TBM8 TBM9 P180 P46T M600 PAY1 PAY2 PAY3
        PAY4 C441 C425 MU2 AC90 AC95 L410 AN24 AN26 C130 C30J C160 P3 E2 C2 V22 KODI
    """,
    Kind.PISTON_TWIN: """
        BE50 BE55 BE56 BE58 BE60 BE65 BE76 BE95 C303 C310 C320 C335 C337 C340 C401 C402 C404
        C414 C421 DA42 DA62 PA23 PA27 PA30 PA31 PA34 PA39 PA44 PA60 P68 AEST BN2P BN2T TECN
        P06T DC3
    """,
    Kind.HIGH_WING: """
        C120 C140 C150 C152 C162 C170 C172 C72R C175 C177 C77R C180 C182 C82R C185 C188 C190
        C195 C205 C206 C207 C210 T206 T210 P210 C06T C82T C82S PA18 PA12 PA16 PA20 PA22 J3
        BL8 BL17 CH7A CH7B HUSK M4 M5 M7 KITF CRUZ GLST DHC2 DHC3 C19 L8 TAYB AR11
        SVNH CC11 CC18 CC19 REMO P92 SKYC
    """,
    Kind.LOW_WING: """
        P28A P28B P28R P28S P28T P28U PA24 PA25 PA32 P32R P32T PA38 PA46
        BE23 BE24 BE33 BE35 BE36 BE19 M20P M20T M20J M20K AA1 AA5 G115 GLAS
        RV3 RV4 RV6 RV7 RV8 RV9 RV10 RV12 RV14 NAVI ERCO COUR T34 T28 P51 SPIT YK52 YK18
        EXTR E300 EA30 PTS2 SIRA SLG2 TB20 TB21 TB10 TB9 TOBA ROBI R200 DR40 Z42 Z43
    """,
    Kind.COMPOSITE: """
        SR20 SR22 S22T COL3 COL4 LNC2 LNC4 LNCE DA20 DA40 DV20 PIVI EVOT GP4 LEG2
        VELO LEZE LGEZ COZY EPIC TL20
    """,
    Kind.HELICOPTER: """
        R22 R44 R66 B06 B06T B105 B204 B205 B206 B212 B222 B230 B407 B412 B427 B429 B430 B47G
        B47J B505 EC20 EC30 EC35 EC45 EC55 EC75 AS50 AS55 AS65 AS3B AS32 AS35 A109 A119 A129
        A139 A149 A169 H500 H269 MD52 MD60 EXPL S76 G2CA EN28 EN48 UH1 UH1Y H1 BK17 GAZL
        ALO2 ALO3 LAMA SCHW S300 S330 H160 KMAX
    """,
    Kind.HEAVY_HELICOPTER: """
        S61 S64 S65 S92 H47 H53 H53S H60 H64 EC25 A189 EH10 NH90 MI8 MI17 MI26 MI24
        KA32 B214 S70 H46
    """,
}

_TYPE_TO_KIND: dict[str, Kind] = {}
for _kind, _codes in _BY_KIND.items():
    for _code in _codes.split():
        # First listing wins, so a type that appears twice keeps its earlier kind.
        _TYPE_TO_KIND.setdefault(_code, _kind)

# ADS-B emitter categories, used when the type is missing or not in the table.
_CATEGORY_TO_KIND: dict[str, Kind] = {
    "A1": Kind.HIGH_WING,  # light, under 15,500 lb
    "A2": Kind.BUSINESS_JET,  # small, 15,500 to 75,000 lb
    "A3": Kind.NARROWBODY,  # large, 75,000 to 300,000 lb
    "A4": Kind.NARROWBODY,  # high-vortex large (Boeing 757)
    "A5": Kind.WIDEBODY,  # heavy, over 300,000 lb
    "A7": Kind.HELICOPTER,  # rotorcraft
    "B1": Kind.COMPOSITE,  # glider or sailplane
    "B4": Kind.HIGH_WING,  # ultralight
}


def classify(type_code: str | None, category: str | None = None) -> Kind:
    if type_code:
        kind = _TYPE_TO_KIND.get(type_code.strip().upper())
        if kind is not None:
            return kind
    if category:
        return _CATEGORY_TO_KIND.get(category.strip().upper(), Kind.UNKNOWN)
    return Kind.UNKNOWN
