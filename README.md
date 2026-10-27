# 旅游攻略生成器（桌面版）

用 Python + Flet 做的桌面小程序：输入城市名，一键生成
景点 / 美食 / 住宿 / 天气 攻略，带自绘序号标注地图和历史记录。

## 运行方式
1. 安装依赖：`pip install -r requirements.txt`
2. 复制 `.env.example` 为 `.env`，填入你自己的高德地图 Web 服务 Key
3. 启动：`python travel_guide.py`

## 说明
- 高德 Key 需自行申请，放入 `.env`，不会进入版本库。
- 天气数据来自 open-meteo，无需 Key。

## License
MIT