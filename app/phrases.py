"""Phrase bank.

Each phrase: Hebrew prompt, Hebrew transliteration hint, category, and accepted variants.
A variant is (arabic, ipa). IPA phonemes are space-separated and words are separated by " | ".
The Arabic text and the IPA must have the same number of words (checked in tests).

Conventions (Palestinian urban speech as the main form):
  ق -> ʔ (urban); a variant with q or k is listed where the rural form is common.
  ث -> t, ذ -> d (normal in the dialect).
  ج -> dʒ.
  Emphatics are written with ˤ (sˤ tˤ dˤ ðˤ), long vowels with ː.
  Geminates are written doubled; the tokenizer collapses them for CTC.
"""

PHRASES = [
    # ---- היכרות ----
    dict(cat="meet", he="שלום", tr="מַרְחַבַּא", variants=[
        ("مرحبا", "m a r ħ a b a"),
        ("اهلا", "ʔ a h l a"),
        ("اهلين", "ʔ a h l eː n"),
    ]),
    dict(cat="meet", he="מה שלומך? (לגבר)", tr="כִּיפַכּ", variants=[
        ("كيفك", "k iː f a k"),
        ("كيف حالك", "k iː f | ħ aː l a k"),
    ]),
    dict(cat="meet", he="אני בסדר, ברוך השם", tr="מְנִיח, אִלְחַמְדִלְלַה", variants=[
        ("منيح الحمد لله", "m n iː ħ | i l ħ a m d | i l l a"),
        ("منيح والحمد لله", "m n iː ħ | w i l ħ a m d | i l l a"),
        ("تمام الحمد لله", "t a m aː m | i l ħ a m d | i l l a"),
    ]),
    dict(cat="meet", he="איך קוראים לך? (לגבר)", tr="שוּ אִסְמַכּ", variants=[
        ("شو اسمك", "ʃ u | ʔ i s m a k"),
        ("شو اسمك", "ʃ u | s m a k"),
        ("ايش اسمك", "ʔ eː ʃ | ʔ i s m a k"),
    ]),
    dict(cat="meet", he="אני מירושלים", tr="אַנַא מִן אִלְקֻדְס", variants=[
        ("انا من القدس", "ʔ a n a | m i n | i l ʔ u d s"),
        ("انا من القدس", "ʔ a n a | m i n | i l q u d s"),
    ]),
    dict(cat="meet", he="בוקר טוב", tr="צַבַּאח אִלְח'ֵיר", variants=[
        ("صباح الخير", "sˤ a b aː ħ | i l x eː r"),
    ]),
    dict(cat="meet", he="לילה טוב (לגבר)", tr="תִצְבַּח עַלַא ח'ֵיר", variants=[
        ("تصبح على خير", "t i sˤ b a ħ | ʕ a l a | x eː r"),
    ]),
    dict(cat="meet", he="תודה רבה", tr="שֻכְּרַן כְּתִיר", variants=[
        ("شكرا كتير", "ʃ u k r a n | k t iː r"),
        ("مشكور كتير", "m a ʃ k uː r | k t iː r"),
    ]),
    dict(cat="meet", he="להתראות", tr="מַע אִסְּסַלַאמֶה", variants=[
        ("مع السلامة", "m a ʕ | i s s a l aː m e"),
        ("يلا باي", "j a l l a | b aː j"),
    ]),
    dict(cat="meet", he="סליחה, תסלח לי", tr="סַאמְחְנִי", variants=[
        ("سامحني", "s aː m ħ i n i"),
        ("اسف", "ʔ aː s i f"),
    ]),
    # ---- שיחה ----
    dict(cat="talk", he="מה אתה עושה? (עכשיו)", tr="שוּ בְּתִעְמַל", variants=[
        ("شو بتعمل", "ʃ u | b t i ʕ m a l"),
        ("شو بتسوي", "ʃ u | b t s a w w i"),
    ]),
    dict(cat="talk", he="אני עובד בבית חולים", tr="בַּשְתְע'ִל בִּלְמֻסְתַשְפַא", variants=[
        ("بشتغل بالمستشفى", "b a ʃ t ɣ i l | b i l m u s t a ʃ f a"),
        ("بشتغل في المستشفى", "b a ʃ t ɣ i l | f i | l m u s t a ʃ f a"),
    ]),
    dict(cat="talk", he="אני לא מבין (גבר)", tr="מִש פַאהֵם", variants=[
        ("مش فاهم", "m i ʃ | f aː h i m"),
        ("ما فهمت", "m aː | f h i m t"),
    ]),
    dict(cat="talk", he="אתה יכול לדבר לאט?", tr="מֻמְכִּן תִחְכִּי שְוַיּ שְוַיּ", variants=[
        ("ممكن تحكي شوي شوي", "m u m k i n | t i ħ k i | ʃ w a j j | ʃ w a j j"),
        ("ممكن تحكي شوي", "m u m k i n | t i ħ k i | ʃ w a j j"),
    ]),
    dict(cat="talk", he="אין בעיה", tr="מִש מֻשְכִּלֶה", variants=[
        ("مش مشكلة", "m i ʃ | m u ʃ k i l e"),
        ("ما في مشكلة", "m aː | f i | m u ʃ k i l e"),
    ]),
    dict(cat="talk", he="נתראה מחר", tr="בְּנְשוּף בַּעְד' בֻּכְּרַה", variants=[
        ("بنشوف بعض بكرة", "b n ʃ uː f | b a ʕ dˤ | b u k r a"),
        ("منشوف بعض بكرة", "m n ʃ uː f | b a ʕ dˤ | b u k r a"),
        ("بنشوفك بكرة", "b n ʃ uː f a k | b u k r a"),
    ]),
    dict(cat="talk", he="היום חם מאוד", tr="אִלְיוֹם שוֹבּ כְּתִיר", variants=[
        ("اليوم شوب كتير", "i l j oː m | ʃ oː b | k t iː r"),
        ("اليوم حر كتير", "i l j oː m | ħ a r r | k t iː r"),
        ("الجو حر كتير", "i l dʒ a w w | ħ a r r | k t iː r"),
    ]),
    # ---- בעיר ----
    dict(cat="city", he="איפה התחנה?", tr="וֵין אִלְמַחַטַּה", variants=[
        ("وين المحطة", "w eː n | i l m a ħ a tˤ tˤ a"),
    ]),
    dict(cat="city", he="איפה השירותים?", tr="וֵין אִלְחַמַּאם", variants=[
        ("وين الحمام", "w eː n | i l ħ a m m aː m"),
    ]),
    dict(cat="city", he="מה השעה?", tr="קַדֵּיש אִסְּסַאעַה", variants=[
        ("قديش الساعة", "ʔ a d d eː ʃ | i s s aː ʕ a"),
        ("قديش الساعة", "q a d d eː ʃ | i s s aː ʕ a"),
        ("كم الساعة", "k a m | i s s aː ʕ a"),
    ]),
    # ---- אוכל וקניות ----
    dict(cat="food", he="אני רוצה כוס קפה", tr="בִּדִּי פִנְג'אן קַהְוֶה", variants=[
        ("بدي فنجان قهوة", "b i d d i | f i n dʒ aː n | ʔ a h w e"),
        ("بدي فنجان قهوة", "b i d d i | f i n dʒ aː n | q a h w e"),
        ("بدي كاسة قهوة", "b i d d i | k aː s e t | ʔ a h w e"),
    ]),
    dict(cat="food", he="תן לי מים, בבקשה", tr="אַעְטִינִי מַיּ לַוּ סַמַחְת", variants=[
        ("اعطيني مي لو سمحت", "ʔ a ʕ tˤ iː n i | m a j j | l a w | s a m a ħ t"),
        ("عطيني مي لو سمحت", "ʕ tˤ iː n i | m a j j | l a w | s a m a ħ t"),
    ]),
    dict(cat="food", he="אני אוהב אוכל ערבי", tr="בַּחִבּ אִלְאַכְּל אִלְעַרַבִּי", variants=[
        ("بحب الأكل العربي", "b a ħ i b b | i l ʔ a k l | i l ʕ a r a b i"),
    ]),
    dict(cat="food", he="כמה זה עולה?", tr="קַדֵּיש חַקּוֹ", variants=[
        ("قديش حقه", "ʔ a d d eː ʃ | ħ a ʔ ʔ o"),
        ("قديش حقه", "q a d d eː ʃ | ħ a q q o"),
        ("بقديش", "b i ʔ a d d eː ʃ"),
    ]),
    dict(cat="food", he="החשבון, בבקשה", tr="אִלְחְסַאבּ לַוּ סַמַחְת", variants=[
        ("الحساب لو سمحت", "i l ħ s aː b | l a w | s a m a ħ t"),
        ("الحساب لو سمحت", "i l ħ i s aː b | l a w | s a m a ħ t"),
    ]),
    # ---- יום 2 ----
    dict(cat="day2", he="את אכלת עגבנייה ליד השולחן", tr="אִנְתִי אַכַּלְתִי בַּנְדוֹרַה עַלַא אֵטַּאוְלֵה", variants=[
        ("انتي اكلتي بندورة على الطاولة", "ʔ i n t i | ʔ a k a l t i | b a n d oː r a | ʕ a l a | i tˤ tˤ aː w l e"),
    ]),
    dict(cat="day2", he="אתה ישבת בשכונה", tr="אִנְתֵ קַעַדְת פִי אֵלְחַארַה", variants=[
        ("انت قعدت في الحارة", "ʔ i n t e | ʔ a ʕ a d t | f i | i l ħ aː r a"),
        ("انت قعدت في الحارة", "ʔ i n t e | q a ʕ a d t | f i | i l ħ aː r a"),
        ("انت قعدت في الحارة", "ʔ i n t a | ʔ a ʕ a d t | f i | i l ħ aː r a"),
        ("انت قعدت في الحارة", "ʔ i n t a | q a ʕ a d t | f i | i l ħ aː r a"),
    ]),
    dict(cat="day2", he="הוא ישב על הכיסא ואכל פלאפל", tr="הוּ קַעַד עַלַא אֵלְכֻּרְסִי וּאַכַּל פַלַאפֵל", variants=[
        ("هو قعد على الكرسي واكل فلافل", "h u w w e | ʔ a ʕ a d | ʕ a l a | i l k u r s i | u ʔ a k a l | f a l aː f i l"),
        ("هو قعد على الكرسي واكل فلافل", "h u w w e | q a ʕ a d | ʕ a l a | i l k u r s i | u ʔ a k a l | f a l aː f i l"),
        ("هو قعد على الكرسي واكل فلافل", "h uː | ʔ a ʕ a d | ʕ a l a | i l k u r s i | u ʔ a k a l | f a l aː f i l"),
        ("هو قعد على الكرسي واكل فلافل", "h uː | q a ʕ a d | ʕ a l a | i l k u r s i | u ʔ a k a l | f a l aː f i l"),
    ]),
    dict(cat="day2", he="היא פתחה את החלון", tr="הִיֵּ פַתְחַת אֵשֻּבַּאכּ", variants=[
        ("هي فتحت الشباك", "h i j j e | f a t ħ a t | i ʃ ʃ u b b aː k"),
        ("هي فتحت الشباك", "h iː | f a t ħ a t | i ʃ ʃ u b b aː k"),
    ]),
    dict(cat="day2", he="אני שאלתי את הרופא", tr="אַנַא סַאַלֵת אֵדַּכְּתוֹר", variants=[
        ("انا سألت الدكتور", "ʔ a n a | s a ʔ a l t | i d d a k t oː r"),
        ("انا سألت الدكتور", "ʔ a n a | s a ʔ a l t | i d d u k t oː r"),
        ("انا سألت الدكتور", "ʔ a n a | s a ʔ a l i t | i d d a k t oː r"),
        ("انا سألت الدكتور", "ʔ a n a | s a ʔ a l i t | i d d u k t oː r"),
    ]),
    dict(cat="day2", he="אנחנו פתחנו את הדלת", tr="אִחְנַא פַתַחְנַא אֵלְבַּאבּ", variants=[
        ("احنا فتحنا الباب", "ʔ i ħ n a | f a t a ħ n a | i l b aː b"),
    ]),
    dict(cat="day2", he="אתם ישבתם ליד השולחן", tr="אִנְתוּ קַעַדְתוּ עַלַא אֵטַּאוְלֵה", variants=[
        ("انتو قعدتو على الطاولة", "ʔ i n t u | ʔ a ʕ a d t u | ʕ a l a | i tˤ tˤ aː w l e"),
        ("انتو قعدتو على الطاولة", "ʔ i n t u | q a ʕ a d t u | ʕ a l a | i tˤ tˤ aː w l e"),
    ]),
    dict(cat="day2", he="הם שאלו את המורה", tr="הֹם סַאַלוּ אֵלְמְעַלֵּם", variants=[
        ("هم سألو المعلم", "h u m m e | s a ʔ a l u | i l m ʕ a l l i m"),
        ("هم سألو المعلم", "h o m | s a ʔ a l u | i l m ʕ a l l i m"),
    ]),
    dict(cat="day2", he="הוא אכל את האוכל על החלון", tr="הוּ אַכַּל אֵלְאַכֵּל עַלַא אֵשֻּבַּאכּ", variants=[
        ("هو اكل الأكل على الشباك", "h u w w e | ʔ a k a l | i l ʔ a k i l | ʕ a l a | i ʃ ʃ u b b aː k"),
        ("هو اكل الأكل على الشباك", "h u w w e | ʔ a k a l | i l ʔ a k l | ʕ a l a | i ʃ ʃ u b b aː k"),
        ("هو اكل الأكل على الشباك", "h uː | ʔ a k a l | i l ʔ a k i l | ʕ a l a | i ʃ ʃ u b b aː k"),
        ("هو اكل الأكل على الشباك", "h uː | ʔ a k a l | i l ʔ a k l | ʕ a l a | i ʃ ʃ u b b aː k"),
    ]),
    dict(cat="day2", he="היא ישבה עם אחיה", tr="הִיֵּ קַעְדַת מַעְ אַחַ'וּהַא", variants=[
        ("هي قعدت مع اخوها", "h i j j e | ʔ a ʕ d a t | m a ʕ | ʔ a x uː h a"),
        ("هي قعدت مع اخوها", "h i j j e | q a ʕ d a t | m a ʕ | ʔ a x uː h a"),
        ("هي قعدت مع اخوها", "h iː | ʔ a ʕ d a t | m a ʕ | ʔ a x uː h a"),
        ("هي قعدت مع اخوها", "h iː | q a ʕ d a t | m a ʕ | ʔ a x uː h a"),
    ]),
    dict(cat="day2", he="את שאלת את החבר", tr="אִנְתִי סַאַלְתִי אֵצַּאחֵבּ", variants=[
        ("انتي سألتي الصاحب", "ʔ i n t i | s a ʔ a l t i | i sˤ sˤ aː ħ i b"),
    ]),
    dict(cat="day2", he="הם פתחו את החלון", tr="הֹם פַתַחוּ אֵשֻּבַּאכּ", variants=[
        ("هم فتحو الشباك", "h u m m e | f a t a ħ u | i ʃ ʃ u b b aː k"),
        ("هم فتحو الشباك", "h o m | f a t a ħ u | i ʃ ʃ u b b aː k"),
    ]),
    dict(cat="day2", he="אתם אכלתם במכונית", tr="אִנְתוּ אַכַּלְתוּ פִי אֵסַּיַּארַה", variants=[
        ("انتو اكلتو في السيارة", "ʔ i n t u | ʔ a k a l t u | f i | i s s a j j aː r a"),
    ]),
    dict(cat="day2", he="אני ישבתי בבית היום", tr="אַנַא קַעַדְת פִי אֵלְבֵּית אֵלְיוֹם", variants=[
        ("انا قعدت في البيت اليوم", "ʔ a n a | ʔ a ʕ a d t | f i | i l b eː t | i l j oː m"),
        ("انا قعدت في البيت اليوم", "ʔ a n a | q a ʕ a d t | f i | i l b eː t | i l j oː m"),
    ]),
    dict(cat="day2", he="אתה פתחת את המטוס", tr="אִנְתֵ פַתַחְת אֵטַּיַּארַה", variants=[
        ("انت فتحت الطيارة", "ʔ i n t e | f a t a ħ t | i tˤ tˤ a j j aː r a"),
        ("انت فتحت الطيارة", "ʔ i n t a | f a t a ħ t | i tˤ tˤ a j j aː r a"),
    ]),
    dict(cat="day2", he="אנחנו שאלנו את הילד שאלה", tr="אִחְנַא סַאַלְנַא אֵלְוַלַד סֻאַאל", variants=[
        ("احنا سألنا الولد سؤال", "ʔ i ħ n a | s a ʔ a l n a | i l w a l a d | s u ʔ aː l"),
    ]),
    dict(cat="day2", he="הוא פתח את הדלת לכלב", tr="הוּ פַתַח אֵלְבַּאבּ לַלְכַּלְבּ", variants=[
        ("هو فتح الباب للكلب", "h u w w e | f a t a ħ | i l b aː b | l a l k a l b"),
        ("هو فتح الباب للكلب", "h uː | f a t a ħ | i l b aː b | l a l k a l b"),
    ]),
    dict(cat="day2", he="היא פתחה את הדלת ואכלה עם אחמד", tr="הִיֵּ פַתְחַת אֵלְבַּאבּ וּאַכְּלַת מַע אַחְמַד", variants=[
        ("هي فتحت الباب واكلت مع احمد", "h i j j e | f a t ħ a t | i l b aː b | u ʔ a k l a t | m a ʕ | ʔ a ħ m a d"),
        ("هي فتحت الباب واكلت مع احمد", "h iː | f a t ħ a t | i l b aː b | u ʔ a k l a t | m a ʕ | ʔ a ħ m a d"),
    ]),
    dict(cat="day2", he="את פתחת את השולחן הגדול", tr="אִנְתִי פַתַחְתִי אֵטַּאוְלֵה אֵלְכְּבִּירֵה", variants=[
        ("انتي فتحتي الطاولة الكبيرة", "ʔ i n t i | f a t a ħ t i | i tˤ tˤ aː w l e | i l k b iː r e"),
    ]),
    dict(cat="day2", he="הם שאלו את החבר שאלה גדולה", tr="הֹם סַאַלוּ אֵצַּאחֵבּ סֻאַאל כְּבִּיר", variants=[
        ("هم سألو الصاحب سؤال كبير", "h u m m e | s a ʔ a l u | i sˤ sˤ aː ħ i b | s u ʔ aː l | k b iː r"),
        ("هم سألو الصاحب سؤال كبير", "h o m | s a ʔ a l u | i sˤ sˤ aː ħ i b | s u ʔ aː l | k b iː r"),
    ]),
    dict(cat="day2", he="אתה אכלת פלאפל ליד השולחן", tr="אִנְתֵ אַכַּלְת פַלַאפֵל עַלַא אֵטַּאוְלֵה", variants=[
        ("انت اكلت فلافل على الطاولة", "ʔ i n t e | ʔ a k a l t | f a l aː f i l | ʕ a l a | i tˤ tˤ aː w l e"),
        ("انت اكلت فلافل على الطاولة", "ʔ i n t a | ʔ a k a l t | f a l aː f i l | ʕ a l a | i tˤ tˤ aː w l e"),
    ]),
    dict(cat="day2", he="אתם פתחתם את הדלת היום", tr="אִנְתוּ פַתַחְתוּ אֵלְבַּאבּ אֵלְיוֹם", variants=[
        ("انتو فتحتو الباب اليوم", "ʔ i n t u | f a t a ħ t u | i l b aː b | i l j oː m"),
    ]),
    dict(cat="day2", he="היא שאלה את הרופא", tr="הִיֵּ סַאְלַת אֵדַּכְּתוֹר", variants=[
        ("هي سألت الدكتور", "h i j j e | s a ʔ l a t | i d d a k t oː r"),
        ("هي سألت الدكتور", "h i j j e | s a ʔ l a t | i d d u k t oː r"),
        ("هي سألت الدكتور", "h iː | s a ʔ l a t | i d d a k t oː r"),
        ("هي سألت الدكتور", "h iː | s a ʔ l a t | i d d u k t oː r"),
    ]),
    dict(cat="day2", he="אני אכלתי עגבנייה עם אבא שלי", tr="אַנַא אַכַּלְת בַּנְדוֹרַה מַע אַבּוּי", variants=[
        ("انا اكلت بندورة مع ابوي", "ʔ a n a | ʔ a k a l t | b a n d oː r a | m a ʕ | ʔ a b uː j"),
    ]),
    dict(cat="day2", he="אנחנו ישבנו בשכונה", tr="אִחְנַא קַעַדְנַא פִי אֵלְחַארַה", variants=[
        ("احنا قعدنا في الحارة", "ʔ i ħ n a | ʔ a ʕ a d n a | f i | i l ħ aː r a"),
        ("احنا قعدنا في الحارة", "ʔ i ħ n a | q a ʕ a d n a | f i | i l ħ aː r a"),
    ]),
    dict(cat="day2", he="הוא שאל את המורה שאלה", tr="הוּ סַאַל אֵלְמְעַלֵּם סֻאַאל", variants=[
        ("هو سأل المعلم سؤال", "h u w w e | s a ʔ a l | i l m ʕ a l l i m | s u ʔ aː l"),
        ("هو سأل المعلم سؤال", "h uː | s a ʔ a l | i l m ʕ a l l i m | s u ʔ aː l"),
    ]),
    dict(cat="day2", he="אתה ישבת עם הכלב", tr="אִנְתֵ קַעַדְת מַעְ אֵלְכַּלְבּ", variants=[
        ("انت قعدت مع الكلب", "ʔ i n t e | ʔ a ʕ a d t | m a ʕ | i l k a l b"),
        ("انت قعدت مع الكلب", "ʔ i n t e | q a ʕ a d t | m a ʕ | i l k a l b"),
        ("انت قعدت مع الكلب", "ʔ i n t a | ʔ a ʕ a d t | m a ʕ | i l k a l b"),
        ("انت قعدت مع الكلب", "ʔ i n t a | q a ʕ a d t | m a ʕ | i l k a l b"),
    ]),
    dict(cat="day2", he="היא אכלה את האוכל על החלון", tr="הִיֵּ אַכְּלַת אֵלְאַכֵּל עַלַא אֵשֻּבַּאכּ", variants=[
        ("هي اكلت الأكل على الشباك", "h i j j e | ʔ a k l a t | i l ʔ a k i l | ʕ a l a | i ʃ ʃ u b b aː k"),
        ("هي اكلت الأكل على الشباك", "h i j j e | ʔ a k l a t | i l ʔ a k l | ʕ a l a | i ʃ ʃ u b b aː k"),
        ("هي اكلت الأكل على الشباك", "h iː | ʔ a k l a t | i l ʔ a k i l | ʕ a l a | i ʃ ʃ u b b aː k"),
        ("هي اكلت الأكل على الشباك", "h iː | ʔ a k l a t | i l ʔ a k l | ʕ a l a | i ʃ ʃ u b b aː k"),
    ]),
    dict(cat="day2", he="את פתחת את המטוס", tr="אִנְתִי פַתַחְתִי אֵטַּיַּארַה", variants=[
        ("انتي فتحتي الطيارة", "ʔ i n t i | f a t a ħ t i | i tˤ tˤ a j j aː r a"),
    ]),
    dict(cat="day2", he="אנחנו שאלנו את החבר וישבנו", tr="אִחְנַא סַאַלְנַא אֵצַּאחֵבּ וּקַעַדְנַא", variants=[
        ("احنا سألنا الصاحب وقعدنا", "ʔ i ħ n a | s a ʔ a l n a | i sˤ sˤ aː ħ i b | u ʔ a ʕ a d n a"),
        ("احنا سألنا الصاحب وقعدنا", "ʔ i ħ n a | s a ʔ a l n a | i sˤ sˤ aː ħ i b | u q a ʕ a d n a"),
    ]),
    dict(cat="day2", he="הם אכלו עגבנייה מהשולחן", tr="הֹם אַכְּלוּ בַּנְדוֹרַה מִן אֵטַּאוְלֵה", variants=[
        ("هم اكلو بندورة من الطاولة", "h u m m e | ʔ a k l u | b a n d oː r a | m i n | i tˤ tˤ aː w l e"),
        ("هم اكلو بندورة من الطاولة", "h u m m e | ʔ a k a l u | b a n d oː r a | m i n | i tˤ tˤ aː w l e"),
        ("هم اكلو بندورة من الطاولة", "h o m | ʔ a k l u | b a n d oː r a | m i n | i tˤ tˤ aː w l e"),
        ("هم اكلو بندورة من الطاولة", "h o m | ʔ a k a l u | b a n d oː r a | m i n | i tˤ tˤ aː w l e"),
    ]),
]

CATEGORIES = [
    {"id": "meet", "name": "היכרות"},
    {"id": "talk", "name": "שיחה"},
    {"id": "city", "name": "בעיר"},
    {"id": "food", "name": "אוכל וקניות"},
    {"id": "day2", "name": "יום 2"},
]


def public_phrases():
    """What the frontend needs: no IPA, first variant's Arabic as the model answer."""
    return [
        {"id": i, "cat": p["cat"], "he": p["he"], "tr": p["tr"], "ar": p["variants"][0][0]}
        for i, p in enumerate(PHRASES)
    ]
