"""finlab — evaluation harness ของงานวิจัย (เฟส 0 ใน 07 - Long-term Roadmap)

หน้าที่เดียวของแพ็กเกจนี้: **บอกให้ได้ว่า algo/factor ไหนจริง อันไหนแค่โชค**

    data.py         universe + ราคาย้อนหลังสำหรับวิจัย (แยกจากคลัง PIT ของ finbg)
    factors.py      factor อ้างอิง + factor ปลอมสำหรับทดสอบตัว harness เอง
    metrics.py      IC, Rank IC, ICIR, decay curve, quantile spread, turnover
    validation.py   walk-forward, purged k-fold + embargo
    stats.py        Sharpe, Probabilistic/Deflated Sharpe, PBO (CSCV)
    costs.py        ต้นทุนซื้อขายตาม turnover
    experiments.py  บันทึกทุกการทดลอง — จำนวนครั้งที่ลองคือตัวแปรของ Deflated Sharpe
    gate.py         ประตู 1: harness ต้องปฏิเสธของปลอมได้ก่อนจะเชื่อของจริง

ทำไมเขียนเองแทน alphalens/Qlib (ตามโน้ต 07): ถ้าไม่เคยเขียนเอง จะไม่รู้ว่าตัวเลขหลอกเราได้ยังไง
แต่ละฟังก์ชันจึงสั้นและมี docstring อธิบายว่าคำนวณอะไร และกับดักอยู่ตรงไหน
"""
