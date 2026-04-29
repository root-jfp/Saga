"""
Generate test PDFs in 12 languages so you can upload them and verify
language detection + voice auto-selection in Saga.

Run: python test_pdfs/generate_test_pdfs.py
Output: test_pdfs/<lang>.pdf
"""

import os
import fitz  # PyMuPDF

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# Map language code -> (font path, sample text)
WIN_FONTS = r"C:\Windows\Fonts"

SAMPLES = {
    "english": {
        "font": os.path.join(WIN_FONTS, "arial.ttf"),
        "title": "The Lighthouse Keeper",
        "text": (
            "On the edge of the cliff stood an old lighthouse, its white walls weathered by a century "
            "of salt wind and rain. Every evening, just before dusk, the keeper would climb the spiral "
            "staircase to light the great lamp at the top. He had done this for thirty years without "
            "missing a single night. The villagers below had long forgotten his name, but they knew "
            "him by the steady beam that swept across the dark water, guiding fishermen safely home. "
            "Some nights, when the storms were fierce, he wondered if anyone still saw the light at all. "
            "But he climbed the stairs anyway. That was the promise he had made to himself, and to the sea."
        ),
    },
    "spanish": {
        "font": os.path.join(WIN_FONTS, "arial.ttf"),
        "title": "El Jardín de Mariposas",
        "text": (
            "En el pequeño pueblo de la sierra había un jardín secreto, escondido detrás de una vieja "
            "casa abandonada. Allí, entre rosales silvestres y arbustos de lavanda, vivían cientos de "
            "mariposas de todos los colores imaginables. Los niños del pueblo creían que el jardín estaba "
            "encantado, y que las mariposas eran en realidad las almas de los antiguos habitantes. "
            "Una tarde de verano, una niña llamada Lucía se atrevió a entrar. Caminó despacio, sin hacer "
            "ruido, y cuando alzó la mano, una mariposa azul se posó en su dedo. Desde ese día, Lucía "
            "regresó al jardín cada tarde, y nunca volvió a sentirse sola."
        ),
    },
    "french": {
        "font": os.path.join(WIN_FONTS, "arial.ttf"),
        "title": "La Boulangerie de Minuit",
        "text": (
            "À Paris, dans une petite rue pavée du Marais, il existe une boulangerie qui n'ouvre qu'à "
            "minuit. Personne ne sait vraiment qui est le boulanger, mais tous ceux qui ont goûté à son "
            "pain disent qu'il a un goût d'enfance, de souvenirs oubliés, de matins d'été. On raconte "
            "qu'il connaît, rien qu'en regardant un client, exactement quel pain il a besoin de manger "
            "ce soir-là. Un homme triste reçoit une brioche dorée; une femme amoureuse, une baguette "
            "encore tiède. Les habitués viennent en silence, déposent quelques pièces, et repartent "
            "avec le sourire. Le boulanger, lui, ne parle jamais. Il sourit, et il pétrit jusqu'à l'aube."
        ),
    },
    "german": {
        "font": os.path.join(WIN_FONTS, "arial.ttf"),
        "title": "Der Uhrmacher von Nürnberg",
        "text": (
            "In einer engen Gasse der Nürnberger Altstadt arbeitete ein alter Uhrmacher, dessen Werkstatt "
            "von hunderten tickender Uhren erfüllt war. Jede Uhr hatte ihre eigene Geschichte, und der "
            "alte Mann konnte stundenlang über die Geheimnisse des Räderwerks sprechen. Doch eine Uhr "
            "stand still, ganz hinten im Regal: eine kleine goldene Taschenuhr, die er vor vielen Jahren "
            "von seinem Vater geerbt hatte. Niemand wusste, warum sie nicht mehr lief. Der Uhrmacher "
            "selbst hatte alles versucht, jedes Zahnrad geprüft, jede Feder gespannt. Aber die Zeit in "
            "dieser kleinen Uhr blieb stehen, als ob sie auf jemanden wartete, der nie zurückkehren würde."
        ),
    },
    "italian": {
        "font": os.path.join(WIN_FONTS, "arial.ttf"),
        "title": "Il Pescatore di Stelle",
        "text": (
            "C'era una volta, in un piccolo villaggio sulla costa della Sicilia, un pescatore che non "
            "pescava pesci. Ogni notte, quando il mare era calmo e il cielo limpido, usciva con la sua "
            "barca e gettava la rete verso le stelle. I bambini del villaggio ridevano di lui, e i "
            "vecchi scuotevano la testa. Ma una notte, dopo anni di tentativi, il pescatore tornò con "
            "qualcosa che brillava nella rete: una piccola stella, ancora calda, che illuminò tutto il "
            "porto. Da quel giorno il villaggio non ebbe mai più bisogno di lampade. E il pescatore, "
            "finalmente, poté riposare in pace, sapendo di aver portato un po' di cielo sulla terra."
        ),
    },
    "portuguese": {
        "font": os.path.join(WIN_FONTS, "arial.ttf"),
        "title": "A Livraria do Fim do Mundo",
        "text": (
            "Numa rua estreita de Lisboa, escondida entre dois prédios antigos, existe uma livraria "
            "que só abre quando chove. O dono é um homem silencioso de cabelos brancos, que parece "
            "saber exatamente qual livro cada cliente precisa ler. Não há catálogo, não há prateleiras "
            "organizadas por género ou autor. Os livros estão empilhados em torres irregulares, "
            "cobrindo até o teto. Mas se entrares com o coração aberto, encontrarás sempre o livro "
            "certo, à tua espera. Dizem que alguns visitantes saem da livraria com livros que não "
            "existem em mais lado nenhum do mundo. E que esses livros mudam-lhes a vida para sempre."
        ),
    },
    "russian": {
        "font": os.path.join(WIN_FONTS, "arial.ttf"),
        "title": "Снежный Поезд",
        "text": (
            "Каждую зиму, ровно в полночь двадцать первого декабря, через маленький северный город "
            "проходит поезд, которого нет ни в одном расписании. Он появляется из метели, бесшумно "
            "останавливается у заброшенной станции и ждёт ровно семь минут. Никто не знает, кто его "
            "машинист, и никто никогда не видел пассажиров. Только следы на снегу остаются после его "
            "ухода — следы людей, которые шли к поезду, но не возвращались. Старики говорят, что этот "
            "поезд везёт души тех, кто потерялся в зимнюю ночь. А дети верят, что он едет прямо в "
            "страну вечного снега, где никто никогда не плачет и не стареет."
        ),
    },
    "japanese": {
        "font": os.path.join(WIN_FONTS, "msgothic.ttc"),
        "title": "夜の図書館",
        "text": (
            "東京の片隅に、夜にしか開かない小さな図書館があります。日が沈むとともに、古い木の扉が"
            "静かに開き、本好きの人々がひとり、またひとりと中に入っていきます。司書は白髪の老婦人で、"
            "彼女は訪れる人の心を見るだけで、その人に必要な本を選んでくれると言われています。"
            "ある雨の夜、若い女性が傘を畳みながら入ってきました。彼女は何を探しているのか自分でもわから"
            "なかったのですが、老婦人は微笑んで一冊の薄い詩集を差し出しました。「これは、あなたが今夜"
            "読むために書かれた本です」と老婦人は言いました。女性はその夜、初めて本当の意味で泣きました。"
            "それは深い悲しみではなく、長い間、心の奥に閉じ込めていた優しさが解き放たれた涙でした。"
        ),
    },
    "chinese": {
        "font": os.path.join(WIN_FONTS, "msyh.ttc"),
        "title": "茶馆里的老人",
        "text": (
            "在杭州西湖边的一条小巷里，有一家不起眼的茶馆，已经经营了三代人。茶馆的主人是一位"
            "白发苍苍的老人，他每天清晨四点起床，亲自烧水，亲自挑选茶叶。来这里喝茶的人不多，但"
            "都是常客，他们说，喝过这里的茶之后，城里其他地方的茶都失去了味道。老人很少说话，但"
            "他听每一位客人讲故事。有时是工作的烦恼，有时是失去的爱情，有时只是关于天气的闲聊。"
            "老人从不给建议，他只是泡茶，倒茶，微笑着点头。可是，奇怪的是，每一个离开茶馆的人，"
            "心里都觉得轻了许多。也许，世界上最珍贵的东西，从来不是答案，而是一个愿意倾听的人。"
        ),
    },
    "korean": {
        "font": os.path.join(WIN_FONTS, "malgun.ttf"),
        "title": "달빛 빵집",
        "text": (
            "서울의 한 골목길에는 보름달이 뜨는 밤에만 문을 여는 작은 빵집이 있다. 주인은 말이 적은 "
            "젊은 여성으로, 그녀가 만드는 빵은 평범해 보이지만, 한 입 베어 물면 잊고 있던 어린 시절의 "
            "기억이 떠오른다고 한다. 어떤 손님은 어머니가 만들어 주시던 따뜻한 찐빵의 맛을 다시 느꼈고, "
            "또 다른 손님은 첫사랑과 함께 나누어 먹었던 단팥빵의 향기를 떠올렸다. 그녀는 손님과 거의 "
            "이야기하지 않지만, 빵을 건네줄 때마다 조용히 미소 짓는다. 사람들은 그녀가 누구이며, 왜 "
            "보름달이 뜨는 밤에만 가게를 여는지 알지 못한다. 하지만 다음 보름달까지, 모든 손님은 "
            "그녀의 빵을 다시 맛보기 위해 기다리고 또 기다린다."
        ),
    },
    "arabic": {
        "font": os.path.join(WIN_FONTS, "tahoma.ttf"),
        "title": "حديقة الذكريات",
        "text": (
            "في قلب مدينة دمشق القديمة، خلف باب خشبي قديم منقوش بأشكال هندسية، تختبئ حديقة صغيرة "
            "لا يعرفها إلا القليل من الناس. تحتوي الحديقة على شجرة ياسمين عمرها أكثر من مئة عام، "
            "ونافورة حجرية تتدفق منها المياه ليلاً ونهاراً دون أن تتوقف. يقال إن من يجلس تحت الشجرة "
            "ويغمض عينيه يستطيع أن يسمع أصوات أحبائه الذين فقدهم. الأطفال يضحكون، الأمهات يغنين، "
            "والآباء يروون قصصاً قديمة. كثير من الزوار يبكون عندما يفتحون عيونهم، ولكنها دموع "
            "السعادة، لا الحزن. صاحب الحديقة شيخ كبير لم يخبر أحداً سر هذا المكان، لكنه يفتح الباب "
            "لكل غريب يطرقه، كأنما يعرف أن قلبه يحتاج إلى ذكرى."
        ),
    },
    "hindi": {
        "font": os.path.join(WIN_FONTS, "Nirmala.ttc"),
        "title": "बारिश की चायवाला",
        "text": (
            "मुंबई की एक संकरी गली में एक बूढ़ा चायवाला है जो केवल बारिश के दिनों में अपनी दुकान खोलता है। "
            "उसकी चाय की खुशबू इतनी अनोखी है कि लोग छाते भूलकर भीगते-भीगते उसके पास पहुंच जाते हैं। "
            "वह बहुत कम बोलता है, बस मुस्कुराकर एक कुल्हड़ चाय थमा देता है। कहते हैं कि उसकी चाय में "
            "कोई जादू है, क्योंकि जो भी उसे पीता है, उसके मन का बोझ हल्का हो जाता है। एक बार एक "
            "नौजवान लड़की वहाँ आई, जो अपने पिता को खो चुकी थी और बहुत उदास थी। उसने चाय पी, और "
            "अचानक उसे अपने पिता की याद आई — उनकी आवाज, उनकी हंसी, उनके हाथों की गर्मी। वह रोई, "
            "लेकिन यह आँसू दर्द के नहीं थे; ये उस प्यार के थे जो कभी खत्म नहीं होता।"
        ),
    },
}


def make_pdf(lang: str, sample: dict, out_path: str) -> None:
    """Generate a single test PDF for *lang*."""
    font_path = sample["font"]
    if not os.path.exists(font_path):
        print(f"[skip {lang}] font not found: {font_path}")
        return

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4

    # Register custom font under a stable alias so insert_textbox can use it.
    fontname = "custom"
    page.insert_font(fontname=fontname, fontfile=font_path)

    # Title
    title_rect = fitz.Rect(60, 60, 535, 110)
    page.insert_textbox(
        title_rect,
        sample["title"],
        fontname=fontname,
        fontsize=22,
        align=fitz.TEXT_ALIGN_CENTER,
    )

    # Body
    body_rect = fitz.Rect(60, 130, 535, 780)
    page.insert_textbox(
        body_rect,
        sample["text"],
        fontname=fontname,
        fontsize=12,
        align=fitz.TEXT_ALIGN_LEFT,
    )

    # Footer with detected-language hint for debugging
    footer_rect = fitz.Rect(60, 790, 535, 820)
    page.insert_textbox(
        footer_rect,
        f"Saga test PDF — language: {lang}",
        fontname=fontname,
        fontsize=8,
        align=fitz.TEXT_ALIGN_CENTER,
    )

    doc.save(out_path)
    doc.close()
    print(f"[ok] {out_path}")


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    for lang, sample in SAMPLES.items():
        out_path = os.path.join(OUT_DIR, f"{lang}.pdf")
        try:
            make_pdf(lang, sample, out_path)
        except Exception as exc:
            print(f"[fail {lang}] {exc}")


if __name__ == "__main__":
    main()
