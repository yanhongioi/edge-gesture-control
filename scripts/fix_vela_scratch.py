"""
修正舊版 Vela 編譯的模型在新版 TFLite (>= 2.16) 載入失敗的問題：
  ValueError: ... required_bytes <= bytes ... Tensor N is invalidly specified in schema.

原因：舊版 Vela 讓 *_scratch / *_scratch_fast 這類執行期暫存 tensor 指向一個「空的 buffer」，
新版 TFLite 會把它當成大小 0 的常數而拒絕載入。把這些 tensor 的 buffer 改成 0
(= 沒有常數資料，執行期再配置)，與新版 Vela 的輸出一致。

需求 (PC 上執行): pip install tensorflow
用法: python scripts/fix_vela_scratch.py board/models/*_vela.tflite
"""
import sys

from tensorflow.lite.tools import flatbuffer_utils


def fix(path):
    model = flatbuffer_utils.read_model(path)
    changed = []
    for subgraph in model.subgraphs:
        for tensor in subgraph.tensors:
            if tensor.buffer == 0:
                continue
            data = model.buffers[tensor.buffer].data
            if data is None or len(data) == 0:
                changed.append(tensor.name.decode() if isinstance(tensor.name, bytes) else tensor.name)
                tensor.buffer = 0
    if changed:
        flatbuffer_utils.write_model(model, path)
    print(f"{path}: {'fixed ' + ', '.join(changed) if changed else 'nothing to fix'}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for p in sys.argv[1:]:
        fix(p)
