# MobileNetSSD_HandAndSKeletonDetect

- 來源：WPI (Weilly Li) 提供的 i.MX 手部偵測 + 手骨架範例（Code Ver 4.0, 2023/04/26）
- 授權：Apache License 2.0，全文見同資料夾 [LICENSE](LICENSE)
- 本專案使用的部分：
  - `board/models/hand_detect_20000_quant*.tflite`（MobileNet-SSD 手部偵測）
  - `board/models/hand_landmark_new_256x256_integer_quant*.tflite`（21 點手骨架）
  - `board/hand_cam.py` 的前後處理流程改寫自原範例的 `app.py`
- 未收錄：另外兩個手骨架模型（`hand_landmark_quant`、`hand_landmark_mediapipe_quant`），Vela 報告顯示 NPU 運算子 0%，在 i.MX93 上只能跑 CPU。

> 注意：原始 `app.py` 檔頭標示 "WPI Confidential Proprietary"，與 LICENSE 不一致。repo 公開前請先向提供者確認。
