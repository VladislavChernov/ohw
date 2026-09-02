import torch

print("🚀 Контейнер работает!")
print(f"Версия PyTorch: {torch.__version__}")
print(f"CUDA доступна: {torch.cuda.is_available()}")

# Создадим тензор на GPU, если есть
if torch.cuda.is_available():
    device = torch.device("cuda")
    x = torch.randn(3, 3).to(device)
    print(f"Тензор создан на устройстве: {x.device}")
else:
    print("⚠️ Работаем на CPU (но это ок для тестов)")
