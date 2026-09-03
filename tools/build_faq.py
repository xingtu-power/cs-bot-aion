#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FAQ 构建器 (Phase 0)
从配置表真实数据自动取值,生成首批 FAQ(kb/{market}/faq/faq.jsonl)。
问题覆盖:产品规格(续航/电池/充电/座位/尺寸/动力/后备箱)、使用指导、服务/质保、经销商。
每条带 intent / keywords / source(可溯源)。
"""
import json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "_lib"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_index(market):
    return json.load(open(os.path.join(ROOT, "kb", market, "spec", "spec_index.json"), encoding="utf-8"))


def find_val(idx, *substrs, variant=None):
    """用归一化后的 field_norm 子串定位字段,返回 (field, {variant:value})。"""
    for fn, v in idx["fields"].items():
        if all(s in fn.lower() for s in substrs):
            return v
    return None


def spec_str(idx, *substrs, prefer_variants=None):
    v = find_val(idx, *substrs)
    if not v:
        return None
    vals = v["variants"]
    if prefer_variants:
        for pv in prefer_variants:
            if pv in vals:
                return vals[pv]
    return list(vals.values())[0]


def faq(market, q, a, intent, keywords, source):
    return {"market": market, "question": q, "answer": a,
            "intent": intent, "keywords": keywords, "source": source,
            "language": "en" if market == "AU" else "th", "id": None}


def build(market):
    idx = load_index(market)
    items = []

    # 通用:数值规格
    battery = spec_str(idx, "battery", "capacity") or spec_str(idx, "ความจุแบตเตอร")
    range_d = spec_str(idx, "range") or spec_str(idx, "ระยะทางที่สามารถขับข")
    seats = spec_str(idx, "seating") or spec_str(idx, "จำนวนที่นั่ง")
    power = spec_str(idx, "max", "power") or spec_str(idx, "กำลังมอเตอร์สูงสุด")
    torque = spec_str(idx, "max", "torque") or spec_str(idx, "แรงบิดมอเตอร์สูงสุด")
    ac = spec_str(idx, "ac", "charging") or spec_str(idx, "กำลังชาร์จสูงสุดแบบ")
    dc = spec_str(idx, "dc", "fast") or spec_str(idx, "กำลังชาร์จไฟฟ้า dc")
    dims = spec_str(idx, "length") or spec_str(idx, "ความยาว")

    lang = "en" if market == "AU" else "th"

    if market == "AU":
        items += [
            faq(market, "What is the driving range of the AION UT?",
                f"The AION UT range under comprehensive working conditions is {range_d}, based on the official spec. Real-world range depends on driving conditions; confirm with the local dealer.",
                "product-inquiry", ["range", "how far", "续航", "driving range"], "spec"),
            faq(market, "What is the battery capacity of the AION UT?",
                f"The AION UT is equipped with a {battery} kWh battery.",
                "product-inquiry", ["battery", "capacity", "kwh"], "spec"),
            faq(market, "What is the max power and torque of the AION UT?",
                f"Max power is {power} kW and max torque is {torque} Nm.",
                "product-inquiry", ["power", "torque", "motor"], "spec"),
            faq(market, "How long does it take to charge the AION UT?",
                f"Max AC charging is {ac}. Max DC fast charging is {dc}, and DC fast charging from 30% to 80% is about 24 min.",
                "product-inquiry", ["charging", "charge", "fast charge"], "spec"),
            faq(market, "How many seats does the AION UT have?",
                f"The AION UT seats {seats} passengers.",
                "product-inquiry", ["seats", "seat", "座位"], "spec"),
            faq(market, "How big is the trunk of the AION UT?",
                "The trunk volume is 321L/689L (standard volume block method).",
                "product-inquiry", ["trunk", "boot", "luggage", "空间"], "spec"),
            faq(market, "What are the dimensions of the AION UT?",
                f"Length x Width x Height is {dims} (mm).",
                "product-inquiry", ["dimensions", "size", "length", "width"], "spec"),
            faq(market, "How do I charge the AION UT?",
                "Refer to the Quick Start Guide / Owner's Manual for charging steps, including opening the charging port and plugging in AC or DC. The AION UT supports AC and DC charging.",
                "usage-guide", ["how to charge", "charging", "charge the car"], "quickstart"),
            faq(market, "How do I start the vehicle?",
                "Refer to the Quick Start Guide for starting the vehicle, including using the smart key and the power button.",
                "usage-guide", ["start", "how to start", "power on"], "quickstart"),
            faq(market, "How do I find a dealer / take a test drive?",
                "I can help locate your nearest dealer from our dealer network and arrange a test drive. Please share your city or postcode, and feel free to leave a contact for a dealer to follow up.",
                "dealer-lookup", ["dealer", "where to buy", "test drive", "nearest"], "dealer"),
            faq(market, "What is the warranty for the AION UT?",
                "Warranty terms are defined by the official GAC / AION policy. Please refer to the Owner's Manual or contact an authorised dealer for exact coverage and terms.",
                "after-sales", ["warranty", "guarantee", "质保"], "owner"),
            faq(market, "What is the service / maintenance interval?",
                "Service intervals are specified in the Owner's Manual. We recommend following the manual and booking service with an authorised dealer.",
                "after-sales", ["service", "maintenance", "保养", "service interval"], "owner"),
            faq(market, "What variants / trims are available for the AION UT?",
                "The AION UT is offered in the " + " / ".join(set(list(idx["fields"].values())[0]["variants"].keys())) + " variants. Please confirm availability with the local dealer.",
                "product-inquiry", ["variants", "trims", "versions", "models"], "spec"),
            faq(market, "How much does the AION UT cost?",
                "Pricing is set by the local market and dealer. I can connect you with a dealer who can provide a personalised quote, or you can download the spec / brochure. Leave a contact for a dealer to follow up.",
                "product-inquiry", ["price", "cost", "how much", "quote"], "dealer"),
            faq(market, "What colours are available for the AION UT?",
                "Colour availability varies by market and stock. Please check with an authorised dealer for the current colour options.",
                "product-inquiry", ["colour", "color", "paint", "colour options"], "dealer"),
            faq(market, "I need roadside assistance / my car broke down.",
                "First make sure you and your passengers are safe and the vehicle is in a safe position. If you are unable to continue, immediately contact the official AION roadside assistance hotline for your region, which is listed in the Emergency Response Guide. I can also provide the nearest dealer / service centre.",
                "emergency", ["roadside", "breakdown", "rescue", "assistance", "won't start"], "rescue"),
            faq(market, "Where can I download the owner's manual?",
                "You can obtain the Owner's Manual, Emergency Response Guide and Quick Start Guide from the AION resources / your dealer. I can summarise specific guidance from the manual for you.",
                "other", ["download", "manual", "quick start", "brochure"], "owner"),
            faq(market, "Can the AION UT be towed?",
                "Towing procedures and restrictions are described in the Owner's Manual. Please follow the manual and contact the rescue service if needed.",
                "usage-guide", ["tow", "towing", "towed"], "owner"),
            faq(market, "What tyre pressure should the AION UT use?",
                "The correct tyre pressure is specified on the tyre placard and in the Owner's Manual. Please check your vehicle's label for the exact value.",
                "usage-guide", ["tyre", "tire", "pressure", "tyre pressure"], "owner"),
            faq(market, "How do I register / insure the AION UT?",
                "Registration and insurance requirements vary by state. Please contact the relevant local transport authority and your insurer; your dealer can also guide you.",
                "other", ["register", "registration", "insurance", "plate"], "dealer"),
        ]
    else:  # THA
        items += [
            faq(market, "AION UT วิ่งได้ไกลเท่าไหร่?",
                f"AION UT วิ่งได้ไกล {range_d} ตามมาตรฐาน NEDC (อ้างอิงจากสเปกอย่างเป็นทางการ)",
                "product-inquiry", ["range", "ระยะทาง", "วิ่งไกล"], "spec"),
            faq(market, "แบตเตอรี่ AION UT มีความจุเท่าไหร่?",
                f"AION UT ติดตั้งแบตเตอรี่ {battery} kWh",
                "product-inquiry", ["แบตเตอรี่", "ความจุ", "battery"], "spec"),
            faq(market, "กำลังและแรงบิดสูงสุดของ AION UT?",
                f"กำลังสูงสุด {power} kW แรงบิดสูงสุด {torque} Nm",
                "product-inquiry", ["กำลัง", "แรงบิด", "power"], "spec"),
            faq(market, "ชาร์จ AION UT ใช้เวลานานเท่าไหร่?",
                f"ชาร์จ AC สูงสุด {ac} ชาร์จ DC สูงสุด {dc}",
                "product-inquiry", ["ชาร์จ", "charging", "charge"], "spec"),
            faq(market, "AION UT มีที่นั่งกี่ที่นั่ง?",
                f"AION UT มี {seats} ที่นั่ง",
                "product-inquiry", ["ที่นั่ง", "seats"], "spec"),
            faq(market, "ชาร์จรถ AION UT อย่างไร?",
                "ดูขั้นตอนการชาร์จในคู่มือการใช้งานขั้นต้น / คู่มือเจ้าของรถ รวมถึงการเปิดฝาช่องชาร์จและเสียบปลั๊ก",
                "usage-guide", ["ชาร์จ", "ช่องชาร์จ", "how to charge"], "quickstart"),
            faq(market, "สตาร์ทรถ AION UT อย่างไร?",
                "ดูคู่มือการใช้งานขั้นต้นสำหรับการสตาร์ทรถ รวมถึงการใช้สมาร์ทคีย์",
                "usage-guide", ["สตาร์ท", "start", "เปิดรถ"], "quickstart"),
            faq(market, "หาโชว์รูม / ทดลองขับได้ที่ไหน?",
                "ฉันช่วยหาตัวแทนจำหน่ายที่ใกล้ที่สุดจากเครือข่ายของเราและนัดทดลองขับได้ รบกวนแชร์เมืองหรือรหัสไปรษณีย์ หรือฝากข้อมูลติดต่อเพื่อให้พนักงานติดต่อกลับ",
                "dealer-lookup", ["โชว์รูม", "ตัวแทน", "ทดลองขับ", "dealer", "ที่ไหนซื้อ"], "dealer"),
            faq(market, "การรับประกันของ AION UT?",
                "เงื่อนไขการรับประกันกำหนดตามนโยบายอย่างเป็นทางการของ GAC / AION โปรดอ้างอิงจากคู่มือเจ้าของรถหรือติดต่อตัวแทนจำหน่ายที่ได้รับอนุญาต",
                "after-sales", ["รับประกัน", "warranty", "ประกัน"], "owner"),
            faq(market, "ระยะเวลาการเข้ารับบริการ / บำรุงรักษา?",
                "รอบการเข้ารับบริการระบุในคู่มือเจ้าของรถ แนะนำให้ปฏิบัติตามคู่มือและนัดหมายบริการกับตัวแทนจำหน่ายที่ได้รับอนุญาต",
                "after-sales", ["บริการ", "บำรุงรักษา", "service", "ศูนย์"], "owner"),
            faq(market, "AION UT มีรุ่นย่อยอะไรบ้าง?",
                "AION UT มีรุ่น " + " / ".join(set(list(idx["fields"].values())[0]["variants"].keys())) + " โปรดสอบถามความพร้อมกับตัวแทนจำหน่ายในพื้นที่",
                "product-inquiry", ["รุ่น", "variant", "trim", "รุ่นย่อย"], "spec"),
            faq(market, "AION UT ราคาเท่าไหร่?",
                "ราคากำหนดตามตลาดและตัวแทนจำหน่ายในพื้นที่ ฉันช่วยเชื่อมต่อกับตัวแทนจำหน่ายเพื่อขอใบเสนอราคา หรือดาวน์โหลดสเปกได้",
                "product-inquiry", ["ราคา", "price", "cost", "เท่าไหร่"], "dealer"),
            faq(market, "AION UT มีสีอะไรบ้าง?",
                "สีขึ้นอยู่กับตลาดและสต็อก โปรดสอบถามตัวแทนจำหน่ายที่ได้รับอนุญาตสำหรับตัวเลือกสีปัจจุบัน",
                "product-inquiry", ["สี", "colour", "color"], "dealer"),
            faq(market, "ขอความช่วยเหลือฉุกเฉิน / รถเสีย?",
                "ก่อนอื่นโปรดตรวจสอบว่าคุณและผู้โดยสารปลอดภัยและรถอยู่ในตำแหน่งที่ปลอดภัย หากไม่สามารถไปต่อได้ ติดต่อสายด่วนช่วยเหลือฉุกเฉินอย่างเป็นทางการของ AION ตามภูมิภาคของคุณทันที ซึ่งระบุในคู่มือการตอบสนองฉุกเฉิน",
                "emergency", ["ฉุกเฉิน", "รถเสีย", "ช่วยเหลือ", "สตาร์ทไม่ติด", "breakdown"], "rescue"),
            faq(market, "ดาวน์โหลดคู่มือได้ที่ไหน?",
                "คุณสามารถรับคู่มือเจ้าของรถ / คู่มือการตอบสนองฉุกเฉิน / คู่มือการใช้งานขั้นต้นได้จากตัวแทนจำหน่าย",
                "other", ["ดาวน์โหลด", "คู่มือ", "manual", "brochure"], "owner"),
            faq(market, "ลากรถ AION UT ได้หรือไม่?",
                "ขั้นตอนและข้อจำกัดในการลากรถระบุในคู่มือเจ้าของรถ โปรดปฏิบัติตามคู่มือและติดต่อบริการฉุกเฉินหากจำเป็น",
                "usage-guide", ["ลาก", "ลากรถ", "tow"], "owner"),
            faq(market, "แรงดันลมยาง AION UT เท่าไหร่?",
                "แรงดันลมยางที่ถูกต้องระบุบนป้ายยางและในคู่มือเจ้าของรถ โปรดตรวจสอบป้ายบนรถของคุณสำหรับค่าที่แน่นอน",
                "usage-guide", ["ลมยาง", "แรงดัน", "tyre", "tire"], "owner"),
            faq(market, "จดทะเบียน / ประกันรถ AION UT อย่างไร?",
                "ข้อกำหนดการจดทะเบียนและประกันแตกต่างกันไปในแต่ละพื้นที่ โปรดติดต่อหน่วยงานขนส่งและบริษัทประกันที่เกี่ยวข้อง ตัวแทนจำหน่ายสามารถแนะนำได้",
                "other", ["ทะเบียน", "จดทะเบียน", "ประกัน", "insurance"], "dealer"),
        ]

    # 编号(稳定):market + 序号
    for i, it in enumerate(items):
        it["id"] = f"faq-{market.lower()}-{i+1:02d}"
    return items


def main():
    for market in ["AU", "THA"]:
        items = build(market)
        out_dir = os.path.join(ROOT, "kb", market, "faq")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "faq.jsonl")
        with open(out_path, "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        print(f"[OK] {market}/faq: {len(items)} entries -> {out_path}")


if __name__ == "__main__":
    main()
