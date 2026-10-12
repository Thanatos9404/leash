"""Offline message templates for FPO managers (Hindi, Marathi, Tamil, Malayalam, English).

With a Nebius key the agents write these messages themselves; these templates are the fallback so the
seller side stays in the manager's language even with no network.
"""

from __future__ import annotations

LANG_NAMES = {"hi": "Hindi", "mr": "Marathi", "ta": "Tamil", "ml": "Malayalam", "en": "English"}

TEMPLATES: dict[str, dict[str, str]] = {
    "quote": {
        "en": "The buyer asked for {qty} kg of {product}. We quoted ${price}/kg.",
        "hi": "खरीदार ने {qty} किलो {product} का भाव मांगा। हमने ${price}/किलो बताया।",
        "mr": "खरेदीदाराने {qty} किलो {product} चा भाव विचारला. आम्ही ${price}/किलो सांगितला.",
        "ta": "வாங்குபவர் {qty} கிலோ {product} விலை கேட்டார். நாங்கள் ${price}/கிலோ என்று சொன்னோம்.",
        "ml": "വാങ്ങുന്നയാൾ {qty} കിലോ {product} വില ചോദിച്ചു. ഞങ്ങൾ ${price}/കിലോ എന്ന് പറഞ്ഞു.",
    },
    "counter": {
        "en": "The buyer offered ${buyer_price}/kg. We countered at ${price}/kg.",
        "hi": "खरीदार ने ${buyer_price}/किलो की पेशकश की। हमने ${price}/किलो पर जवाबी प्रस्ताव दिया।",
        "mr": "खरेदीदाराने ${buyer_price}/किलो देऊ केले. आम्ही ${price}/किलो असा प्रतिप्रस्ताव दिला.",
        "ta": "வாங்குபவர் ${buyer_price}/கிலோ தர முன்வந்தார். நாங்கள் ${price}/கிலோ என்று எதிர் விலை சொன்னோம்.",
        "ml": "വാങ്ങുന്നയാൾ ${buyer_price}/കിലോ വാഗ്ദാനം ചെയ്തു. ഞങ്ങൾ ${price}/കിലോ എന്ന് മറുവില പറഞ്ഞു.",
    },
    "accept": {
        "en": "Deal agreed: {qty} kg of {product} at ${price}/kg.",
        "hi": "सौदा तय: {qty} किलो {product}, ${price}/किलो।",
        "mr": "सौदा पक्का: {qty} किलो {product}, ${price}/किलो.",
        "ta": "ஒப்பந்தம் உறுதி: {qty} கிலோ {product}, ${price}/கிலோ.",
        "ml": "കരാർ ഉറപ്പിച്ചു: {qty} കിലോ {product}, ${price}/കിലോ.",
    },
    "reject": {
        "en": "The buyer's offer of ${buyer_price}/kg was below our minimum. No deal this time.",
        "hi": "खरीदार की ${buyer_price}/किलो की पेशकश हमारी न्यूनतम कीमत से कम थी। इस बार सौदा नहीं हुआ।",
        "mr": "खरेदीदाराची ${buyer_price}/किलो ची ऑफर आमच्या किमान किमतीपेक्षा कमी होती. या वेळी सौदा झाला नाही.",
        "ta": "வாங்குபவரின் ${buyer_price}/கிலோ விலை எங்கள் குறைந்தபட்ச விலையை விடக் குறைவு. இந்த முறை ஒப்பந்தம் இல்லை.",
        "ml": "വാങ്ങുന്നയാളുടെ ${buyer_price}/കിലോ ഓഫർ ഞങ്ങളുടെ കുറഞ്ഞ വിലയേക്കാൾ താഴെയായിരുന്നു. ഇത്തവണ കരാറില്ല.",
    },
    "order": {
        "en": (
            "New confirmed order from {buyer}: {qty} kg of {product} at ${price}/kg (total ${value}). "
            "The buyer has paid a {pct}% advance through PayPal. Your share of ${share} is released to your "
            "PayPal account as soon as you report dispatch with a tracking number."
        ),
        "hi": (
            "{buyer} से नया पक्का ऑर्डर: {product} {qty} किलो, ${price}/किलो (कुल ${value})। खरीदार ने PayPal से "
            "{pct}% अग्रिम भुगतान कर दिया है। ट्रैकिंग नंबर के साथ डिस्पैच की सूचना देते ही आपका ${share} हिस्सा "
            "आपके PayPal खाते में भेज दिया जाएगा।"
        ),
        "mr": (
            "{buyer} कडून नवीन निश्चित ऑर्डर: {product} {qty} किलो, ${price}/किलो (एकूण ${value}). खरेदीदाराने PayPal "
            "द्वारे {pct}% आगाऊ रक्कम भरली आहे. ट्रॅकिंग नंबरसह डिस्पॅचची माहिती देताच तुमचा ${share} हिस्सा तुमच्या "
            "PayPal खात्यात पाठवला जाईल."
        ),
        "ta": (
            "{buyer} இடமிருந்து புதிய உறுதியான ஆர்டர்: {product} {qty} கிலோ, ${price}/கிலோ (மொத்தம் ${value}). "
            "வாங்குபவர் PayPal மூலம் {pct}% முன்பணம் செலுத்தியுள்ளார். டிராக்கிங் எண்ணுடன் அனுப்பியதைத் "
            "தெரிவித்தவுடன் உங்கள் பங்கு ${share} உங்கள் PayPal கணக்கிற்கு அனுப்பப்படும்."
        ),
        "ml": (
            "{buyer} ൽ നിന്ന് പുതിയ സ്ഥിരീകരിച്ച ഓർഡർ: {product} {qty} കിലോ, ${price}/കിലോ (ആകെ ${value}). "
            "വാങ്ങുന്നയാൾ PayPal വഴി {pct}% മുൻകൂർ തുക അടച്ചു. ട്രാക്കിംഗ് നമ്പറോടെ ഡിസ്പാച്ച് അറിയിച്ചാലുടൻ "
            "നിങ്ങളുടെ ${share} വിഹിതം നിങ്ങളുടെ PayPal അക്കൗണ്ടിലേക്ക് അയയ്ക്കും."
        ),
    },
    "payout": {
        "en": "PayPal payout sent: ${amount} for {qty} kg of {product} dispatched (tracking {tracking}). Batch {batch}.",
        "hi": "PayPal भुगतान भेजा गया: डिस्पैच किए गए {product} {qty} किलो के लिए ${amount} (ट्रैकिंग {tracking})। बैच {batch}।",
        "mr": "PayPal पेमेंट पाठवले: डिस्पॅच केलेल्या {product} {qty} किलोसाठी ${amount} (ट्रॅकिंग {tracking}). बॅच {batch}.",
        "ta": "PayPal பணம் அனுப்பப்பட்டது: அனுப்பிய {product} {qty} கிலோவுக்கு ${amount} (டிராக்கிங் {tracking}). பேட்ச் {batch}.",
        "ml": "PayPal പേയ്‌മെന്റ് അയച്ചു: ഡിസ്പാച്ച് ചെയ്ത {product} {qty} കിലോയ്ക്ക് ${amount} (ട്രാക്കിംഗ് {tracking}). ബാച്ച് {batch}.",
    },
    "short": {
        "en": "Recorded a shortfall of {short} kg of {product}. The buyer gets ${refund} of the advance back through PayPal.",
        "hi": "{product} में {short} किलो की कमी दर्ज हुई। खरीदार को अग्रिम में से ${refund} PayPal से वापस किए जाएंगे।",
        "mr": "{product} मध्ये {short} किलोची कमतरता नोंदवली. खरेदीदाराला आगाऊ रकमेतून ${refund} PayPal द्वारे परत केले जातील.",
        "ta": "{product} இல் {short} கிலோ குறைவு பதிவு செய்யப்பட்டது. வாங்குபவருக்கு முன்பணத்தில் ${refund} PayPal மூலம் திருப்பித் தரப்படும்.",
        "ml": "{product} ൽ {short} കിലോ കുറവ് രേഖപ്പെടുത്തി. വാങ്ങുന്നയാൾക്ക് മുൻകൂർ തുകയിൽ നിന്ന് ${refund} PayPal വഴി തിരികെ നൽകും.",
    },
    "balance": {
        "en": "Balance released via PayPal: ${amount} after the buyer confirmed receipt. Batch {batch}.",
        "hi": "शेष भुगतान PayPal से जारी: खरीदार द्वारा माल मिलने की पुष्टि के बाद ${amount}। बैच {batch}।",
        "mr": "उर्वरित रक्कम PayPal द्वारे जारी: खरेदीदाराने माल मिळाल्याची पुष्टी केल्यानंतर ${amount}. बॅच {batch}.",
        "ta": "மீதித் தொகை PayPal மூலம் விடுவிக்கப்பட்டது: வாங்குபவர் பொருள் கிடைத்ததை உறுதிப்படுத்திய பின் ${amount}. பேட்ச் {batch}.",
        "ml": "ബാക്കി തുക PayPal വഴി നൽകി: സാധനം ലഭിച്ചതായി വാങ്ങുന്നയാൾ സ്ഥിരീകരിച്ചതിന് ശേഷം ${amount}. ബാച്ച് {batch}.",
    },
}


def render(kind: str, lang: str, product_en: str = "", product_local: str = "", **values) -> tuple[str, str]:
    """Return (local_text, english_text). Product names are passed separately so each text uses its own."""
    table = TEMPLATES[kind]
    local = table.get(lang, table["en"]).format(product=product_local or product_en, **values)
    english = table["en"].format(product=product_en, **values)
    return local, english
