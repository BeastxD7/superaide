# ruff: noqa: E501
"""Realistic conversations for the Airlock measurement (issue #128): names, phones, e-mails, addresses and ids in
English, Hindi and Spanish; quoted and forwarded mail; names inside URLs; names that are also common words.

Each case lists what the model must NEVER see (`private`), what is only listed to the airlock as a contact
(`names`), what is the owner's own and may stay (`keep`), and ordinary text that must come through untouched
(`plain`). Every value is invented."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Case:
    id: str
    lang: str
    text: str
    private: list[str]
    names: list[str] = field(default_factory=list)
    keep: list[str] = field(default_factory=list)
    plain: list[str] = field(default_factory=list)


OWNER = ["Shashank Rao", "Shashank", "shashank@owner-example.com", "+65 9000 1111"]

CASES: list[Case] = [
    # --- English -----------------------------------------------------------------------------------------------
    Case(
        "en-phone-email",
        "en",
        "Priya Tan asked me to call her on +65 8123 4567 or write to priya.tan@acme-example.com before Friday.",
        ["Priya Tan", "8123 4567", "priya.tan@acme-example.com"],
        ["Priya Tan"],
        plain=["before Friday"],
    ),
    Case(
        "en-first-name-only",
        "en",
        "Can you tell Priya that the 3pm moved to 4pm?",
        ["Priya"],
        ["Priya Tan"],
        plain=["3pm moved to 4pm"],
    ),
    Case(
        "en-address-sg",
        "en",
        "Send the parcel to 12 Orchard Road #05-01, Singapore 238841 for Daniel Koh.",
        ["12 Orchard Road", "238841", "Daniel Koh"],
        ["Daniel Koh"],
    ),
    Case(
        "en-address-us",
        "en",
        "His place is 1600 Pennsylvania Avenue, Washington, DC 20500 if you need it.",
        ["1600 Pennsylvania Avenue", "20500"],
        [],
    ),
    Case(
        "en-address-label",
        "en",
        "Address: Flat 4B, Rosewood Court, Elm Lane, Bristol BS1 4DJ\nThanks!",
        ["Flat 4B", "Rosewood Court", "BS1 4DJ"],
        [],
    ),
    Case(
        "en-ssn-card",
        "en",
        "Her SSN is 123-45-6789 and the card 4242 4242 4242 4242 expires 12/28.",
        ["123-45-6789", "4242 4242 4242 4242"],
        [],
        plain=["expires 12/28"],
    ),
    Case(
        "en-iban",
        "en",
        "Pay Marta at IBAN GB82 WEST 1234 5698 7654 32 please.",
        ["GB82 WEST 1234 5698 7654 32", "Marta"],
        ["Marta Diaz"],
    ),
    Case(
        "en-passport",
        "en",
        "My assistant has her passport A1234567 and NRIC S1234567D on file.",
        ["A1234567", "S1234567D"],
        [],
    ),
    Case("en-ip-handle", "en", "Server 192.168.1.20 is run by @priya_t on Telegram.", ["192.168.1.20", "@priya_t"], []),
    Case(
        "en-owner-kept",
        "en",
        "Reply to me at shashank@owner-example.com or +65 9000 1111; Sam Lee is at sam.lee@other-example.com.",
        ["sam.lee@other-example.com", "Sam Lee"],
        ["Sam Lee"],
        keep=OWNER,
        plain=["shashank@owner-example.com", "+65 9000 1111"],
    ),
    Case(
        "en-common-word-name",
        "en",
        "Will Rose said he will bring the rose bushes. Ask Will about it.",
        ["Will Rose"],
        ["Will Rose"],
    ),
    Case(
        "en-common-word-name-2",
        "en",
        "Mark the date: Mark is arriving on the 5th; hope Grace gets there too.",
        ["Mark", "Grace"],
        ["Mark Hope", "Grace Ng"],
        plain=["on the 5th"],
    ),
    Case(
        "en-url-name",
        "en",
        "Her profile is https://www.linkedin.com/in/priya-tan-8a7b21 and https://acme-example.com/u/priya.tan",
        ["priya-tan", "priya.tan"],
        ["Priya Tan"],
    ),
    Case(
        "en-url-encoded",
        "en",
        "Open https://acme-example.com/search?name=Priya%20Tan&email=p%40acme-example.com",
        ["Priya%20Tan"],
        ["Priya Tan"],
    ),
    Case(
        "en-mailto",
        "en",
        "Click mailto:priya.tan@acme-example.com?subject=Hello to write to her.",
        ["priya.tan@acme-example.com"],
        [],
    ),
    Case(
        "en-forwarded",
        "en",
        "FYI below.\n\n-----Original Message-----\nFrom: Daniel Koh <daniel.koh@acme-example.com>\nTo: Shashank Rao "
        '<shashank@owner-example.com>\nCc: "Marta Diaz" <marta@diaz-example.es>, Priya Tan <priya@acme-example.com>\n'
        "Subject: Lunch\n\n> Hi Shashank, call me on 020 7946 0958 about the offsite.\n> Daniel",
        [
            "Daniel Koh",
            "daniel.koh@acme-example.com",
            "Marta Diaz",
            "marta@diaz-example.es",
            "priya@acme-example.com",
            "020 7946 0958",
        ],
        ["Daniel Koh"],
        keep=OWNER,
        plain=["Subject: Lunch", "about the offsite"],
    ),
    Case(
        "en-quoted-reply",
        "en",
        "On Tue, Oct 20, Priya Tan <priya@acme-example.com> wrote:\n> Please send the file to my home: 42 Elm Street, "
        "Springfield 62704.\n> Thanks, Priya",
        ["Priya Tan", "priya@acme-example.com", "42 Elm Street", "62704"],
        ["Priya Tan"],
    ),
    Case(
        "en-two-contacts",
        "en",
        "Tell Anna Lee and Annabel Cho that Anna is late. Annabel knows.",
        ["Anna Lee", "Annabel Cho", "Annabel"],
        ["Anna Lee", "Annabel Cho"],
    ),
    Case("en-name-with-accent-typed-plain", "en", "Ask Jose Garcia to ring back.", ["Jose Garcia"], ["José García"]),
    # --- Spanish -----------------------------------------------------------------------------------------------
    Case(
        "es-phone-email",
        "es",
        "Llama a María José Ortega al +34 612 345 678 o escribe a maria.ortega@correo-example.es, por favor.",
        ["María José Ortega", "612 345 678", "maria.ortega@correo-example.es"],
        ["María José Ortega"],
        plain=["por favor"],
    ),
    Case("es-bare-mobile", "es", "Su móvil es 612345678, llámala por la tarde.", ["612345678"], []),
    Case(
        "es-address",
        "es",
        "Vive en Calle Mayor 12, 3º B, 28013 Madrid, justo al lado del metro.",
        ["Calle Mayor 12", "28013"],
        [],
        plain=["justo al lado del metro"],
    ),
    Case(
        "es-address-label",
        "es",
        "Dirección: Avenida de la Constitución 45, 41001 Sevilla\nGracias.",
        ["Avenida de la Constitución 45", "41001"],
        [],
    ),
    Case("es-dni", "es", "Su DNI es 12345678Z y el NIE X1234567L, no lo pierdas.", ["12345678Z", "X1234567L"], []),
    Case(
        "es-forwarded",
        "es",
        "Reenviado:\nDe: Carlos Núñez <carlos.nunez@empresa-example.es>\nPara: Shashank Rao <shashank@owner-example.com>\n"
        "Asunto: Reunión\n\n> Hola, soy Carlos. Mi teléfono es 91 555 12 34.",
        ["Carlos Núñez", "carlos.nunez@empresa-example.es"],
        ["Carlos Núñez"],
        keep=OWNER,
        plain=["Asunto: Reunión"],
    ),
    Case(
        "es-name-no-accent",
        "es",
        "Dile a Maria Jose que la reunión sigue en pie.",
        ["Maria Jose"],
        ["María José Ortega"],
        plain=["la reunión sigue en pie"],
    ),
    Case(
        "es-url-name",
        "es",
        "Su perfil: https://empresa-example.es/equipo/carlos-nunez",
        ["carlos-nunez"],
        ["Carlos Núñez"],
    ),
    # --- Hindi -------------------------------------------------------------------------------------------------
    Case(
        "hi-phone",
        "hi",
        "कृपया प्रिया शर्मा को +91 98765 43210 पर कॉल करें।",
        ["प्रिया शर्मा", "98765 43210"],
        ["प्रिया शर्मा"],
        plain=["कृपया"],
    ),
    Case("hi-bare-mobile", "hi", "उनका नंबर 9876543210 है, शाम को फोन करना।", ["9876543210"], []),
    Case("hi-devanagari-digits", "hi", "उनका नंबर ९८७६५ ४३२१० है।", ["९८७६५ ४३२१०"], []),
    Case(
        "hi-email",
        "hi",
        "ईमेल करें: priya.sharma@udyog-example.in धन्यवाद।",
        ["priya.sharma@udyog-example.in"],
        [],
        plain=["धन्यवाद"],
    ),
    Case(
        "hi-address-label",
        "hi",
        "पता: मकान नंबर १२, गली ४, सेक्टर ५, नोएडा\nधन्यवाद।",
        ["मकान नंबर १२", "गली ४", "सेक्टर ५"],
        [],
        plain=["धन्यवाद"],
    ),
    Case(
        "hi-aadhaar-pan",
        "hi",
        "आधार 2345 6789 0123 और PAN ABCDE1234F दोनों भेज दिए हैं।",
        ["2345 6789 0123", "ABCDE1234F"],
        [],
        plain=["भेज दिए हैं"],
    ),
    Case(
        "hi-name-in-english-chat",
        "hi",
        "Priya Sharma ने कहा कि मीटिंग कल है।",
        ["Priya Sharma"],
        ["Priya Sharma"],
        plain=["मीटिंग कल है"],
    ),
    Case(
        "hi-forwarded",
        "hi",
        "आगे भेजा गया:\nप्रेषक: राहुल वर्मा <rahul.verma@udyog-example.in>\nविषय: बैठक\n\n> नमस्ते, मेरा नंबर 9123456780 है।",
        ["राहुल वर्मा", "rahul.verma@udyog-example.in", "9123456780"],
        ["राहुल वर्मा"],
        plain=["विषय: बैठक"],
    ),
    # --- Mixed / tool results ------------------------------------------------------------------------------------
    Case(
        "mixed-code-switch",
        "en",
        "Priya ko bolo ki Daniel Koh ka number +65 9123 8765 hai aur address 7 Jalan Besar, Singapore 208787.",
        ["Priya", "Daniel Koh", "9123 8765", "7 Jalan Besar", "208787"],
        ["Priya Tan", "Daniel Koh"],
    ),
    Case(
        "search-result-list",
        "en",
        "Found 3 contacts:\n1. Marta Diaz, marta@diaz-example.es, +34 699 111 222\n2. Daniel Koh, daniel.koh@acme-example.com\n"
        "3. Priya Tan, priya@acme-example.com, +65 8123 4567",
        [
            "Marta Diaz",
            "marta@diaz-example.es",
            "699 111 222",
            "daniel.koh@acme-example.com",
            "priya@acme-example.com",
            "8123 4567",
        ],
        ["Marta Diaz", "Daniel Koh", "Priya Tan"],
        plain=["Found 3 contacts"],
    ),
]

# Ordinary text that must survive untouched (no over-redaction of dates, money, versions, times, counts).
PLAIN_ONLY = [
    "Meet on 2026-10-23 at 10:30, invoice 1,250.00, 3 people, order 12345, version 1.2.3.",
    "El pedido 4821 llega el 12/11/2026 por 35,50 euros.",
    "बैठक 23/10/2026 को 10:30 बजे, कुल 3 लोग।",
    "Chapter 7, page 142, room 3B, flight SQ 321 departs at 08:45.",
]
