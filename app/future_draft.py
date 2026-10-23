"""Draft future forms of stage A's verbs (שלב א'): those that LEX does not have yet.

Made by rule from the forms LEX has (another prefix letter on a known form, or ـي / ـو added), so they are NOT checked.
The verb game shows a verb whose future is not all in LEX to teachers only, marked טיוטה. Once a teacher has checked
a form, move it into LEX (workbook.py) and delete it here: the verb then reaches students when all eight are in LEX.
The review list is docs/future-forms-draft.md.
"""

FUTURE_DRAFT: dict[str, tuple[str, list[str]]] = {
    # يفتح
    "تفتح": ("תִפְתַח", ["t i f t a ħ"]),
    "تفتحي": ("תִפְתַחִי", ["t i f t a ħ i"]),
    "نفتح": ("נִפְתַח", ["n i f t a ħ"]),
    "تفتحو": ("תִפְתַחוּ", ["t i f t a ħ u"]),
    "يفتحو": ("יִפְתַחוּ", ["j i f t a ħ u"]),
    # ياكل
    "ياكلو": ("יֹאכְּלוּ", ["j oː k u l u", "j oː k i l u", "j aː k u l u", "j oː k l u", "j aː k l u"]),
    # يدخل
    "تدخلي": ("תֻדְחֹ'לִי", ["t u d x u l i"]),
    "تدخلو": ("תֻדְחֹ'לוּ", ["t u d x u l u"]),
    "يدخلو": ("יֻדְחֹ'לוּ", ["j u d x u l u"]),
    # يسبح
    "اسبح": ("אַסְבַּח", ["ʔ a s b a ħ"]),
    "تسبحي": ("תִסְבַּחִי", ["t i s b a ħ i"]),
    "تسبحو": ("תִסְבַּחוּ", ["t i s b a ħ u"]),
    "يسبحو": ("יִסְבַּחוּ", ["j i s b a ħ u"]),
    # يطبخ
    "تطبخو": ("תֻטְבֹּח'וּ", ["t u tˤ b u x u"]),
    "يطبخو": ("יֻטְבֹּח'וּ", ["j u tˤ b u x u"]),
    # ينقل
    "انقل": ("אַנְקֹל", ["ʔ a n ʔ u l", "ʔ a n q u l"]),
    "تنقلي": ("תֻנְקֹלִי", ["t u n ʔ u l i", "t u n q u l i"]),
    "ننقل": ("נֻנְקֹל", ["n u n ʔ u l", "n u n q u l"]),
    "تنقلو": ("תֻנְקֹלוּ", ["t u n ʔ u l u", "t u n q u l u"]),
    # يعطي
    "نعطي": ("נֵעַטִי", ["n a ʕ tˤ i", "n i ʕ tˤ i"]),
    "تعطو": ("תֵעַטוּ", ["t a ʕ tˤ u", "t i ʕ tˤ u"]),
    "يعطو": ("יֵעַטוּ", ["j a ʕ tˤ u", "j i ʕ tˤ u"]),
    # يشتري
    "تشترو": ("תִשְתְרוּ", ["t i ʃ t r u", "t i ʃ t i r u"]),
    "يشترو": ("יִשְתְרוּ", ["j i ʃ t r u", "j i ʃ t i r u"]),
    # يروح
    "تروحو": ("תְרוּחוּ", ["t r uː ħ u", "t i r uː ħ u"]),
    # يقول
    "تقولي": ("תְקוּלִי", ["t ʔ uː l i", "t q uː l i"]),
    "نقول": ("נְקוּל", ["n ʔ uː l", "n q uː l"]),
    "تقولو": ("תְקוּלוּ", ["t ʔ uː l u", "t q uː l u"]),
    "يقولو": ("יִקוּלוּ", ["j ʔ uː l u", "i ʔ uː l u", "j q uː l u", "j i ʔ uː l u"]),
    # يكون
    "تكونو": ("תְכּוּנוּ", ["t k uː n u"]),
    "يكونو": ("יִכּוּנוּ", ["j k uː n u", "i k uː n u", "j i k uː n u"]),
    # يبوس
    "تبوسي": ("תְבּוּסִי", ["t b uː s i"]),
    "نبوس": ("נְבּוּס", ["n b uː s"]),
    "تبوسو": ("תְבּוּסוּ", ["t b uː s u"]),
    "يبوسو": ("יִבּוּסוּ", ["j b uː s u", "i b uː s u", "j i b uː s u"]),
    # يسوق
    "نسوق": ("נְסוּק", ["n s uː ʔ", "n s uː q"]),
    "تسوقو": ("תְסוּקוּ", ["t s uː ʔ u", "t s uː q u"]),
    "يسوقو": ("יִסוּקוּ", ["j s uː ʔ u", "j s uː q u", "i s uː ʔ u", "j i s uː ʔ u"]),
    # يفوت
    "تفوتي": ("תְפוּתִי", ["t f uː t i"]),
    "نفوت": ("נְפוּת", ["n f uː t"]),
    "تفوتو": ("תְפוּתוּ", ["t f uː t u"]),
    "يفوتو": ("יִפוּתוּ", ["j f uː t u", "i f uː t u", "j i f uː t u"]),
    # ياخد
    "ناخد": ("נֹאחֵ'ד", ["n oː x i d", "n aː x u d"]),
    "تاخدو": ("תֹאחְ'דוּ", ["t oː x i d u", "t aː x u d u", "t oː x d u", "t aː x d u", "t oː x u d u"]),
    # يفهم
    "نفهم": ("נִפְהַם", ["n i f h a m"]),
    "تفهمو": ("תִפְהַמוּ", ["t i f h a m u"]),
    # يرجع
    "نرجع": ("נִרְגַ'ע", ["n i r dʒ a ʕ"]),
    # يشكر
    "اشكر": ("אַשְכֹּר", ["ʔ a ʃ k u r"]),
    "تشكر": ("תֻשְכֹּר", ["t u ʃ k u r"]),
    "تشكري": ("תֻשְכֹּרִי", ["t u ʃ k u r i"]),
    "تشكرو": ("תֻשְכֹּרוּ", ["t u ʃ k u r u"]),
    "يشكرو": ("יֻשְכֹּרוּ", ["j u ʃ k u r u"]),
    # ينزل
    "انزل": ("אַנְזֵל", ["ʔ a n z i l", "ʔ a n z a l"]),
    "تنزلي": ("תִנְזֵלִי", ["t i n z i l i", "t i n z a l i"]),
    "ننزل": ("נִנְזֵל", ["n i n z i l", "n i n z a l"]),
    "ينزلو": ("יִנְזְלוּ", ["j i n z i l u", "j i n z a l u", "j i n z l u"]),
    # يوصل
    "توصلو": ("תוֹצַלוּ", ["t oː sˤ a l u"]),
    # يضحك
    "يضحكو": ("יִדְֿחַכּוּ", ["j i dˤ ħ a k u"]),
    # يلبس
    "البس": ("אַלְבַּס", ["ʔ a l b a s"]),
    "نلبس": ("נִלְבַּס", ["n i l b a s"]),
    "تلبسو": ("תִלְבַּסוּ", ["t i l b a s u"]),
    "يلبسو": ("יִלְבַּסוּ", ["j i l b a s u"]),
}
