"""
旅游攻略生成器（面向对象 + Flet 桌面窗口版）

功能：
  - 通过高德地图 Web 服务拉取城市的景点 / 美食 / 住宿
  - 通过 open-meteo 拉取未来几天的天气
  - 把攻略存成 JSON，支持查看历史
  - 用 Flet 做一个弹窗式桌面 GUI，替代原来的控制台

运行：
  pip install flet requests
  python travel_guide.py        # 弹出桌面窗口
  # 以后想用手机/浏览器访问，改成：
  # ft.run(main, view=ft.AppView.WEB_BROWSER)
"""

import asyncio
import base64
import json
import os
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None

import flet as ft

# 以脚本所在目录为基准，guides / usage.json 都放在这里，避免依赖“在哪运行”
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GUIDE_DIR = os.path.join(BASE_DIR, "guides")
USAGE_FILE = os.path.join(BASE_DIR, "usage.json")
CITY_BG_DIR = os.path.join(BASE_DIR, "city_bg")  # 放城市背景图：如 北京.jpg；没有就用高德地图兜底

AMAP_KEY = os.environ.get("AMAP_KEY", "你的高德地图API Key")

NATIONAL_TIPS = {
    "北京": [
        "故宫、天安门、国家博物馆等热门场馆建议提前预约。",
        "八达岭长城距离市区较远，建议预留半天以上时间。",
        "北京地铁覆盖主要景点，出行优先选择公共交通。",
        "秋冬季节早晚温差较大，注意添衣保暖。",
        "王府井、南锣鼓巷、前门大街适合逛街和吃小吃。",
    ],
}


class POIService:
    """负责和高德地图打交道：按关键词搜索城市的 POI。"""

    def __init__(self, api_key):
        self.api_key = api_key

    def _check_amap_quota(self, resp):
        """只使用高德返回的真实剩余额度，不做本地估算。返回给上层展示用的提示语。"""
        header_remaining = resp.headers.get("x-remaining-request")
        if header_remaining is not None:
            try:
                rem = int(header_remaining)
                if rem <= 50:
                    return f"高德地图剩余请求额度较低：{rem} 次"
                return f"高德地图剩余请求额度：{rem} 次"
            except (ValueError, TypeError):
                pass
        return "本次高德接口未返回真实剩余额度，请前往高德开放平台控制台查看。"

    def fetch_top_pois(self, city, keyword="景点", max_pages=2, offset=10):
        """返回 (pois, 提示语)。pois 为字典列表。"""
        if not self.api_key or self.api_key == "你的高德地图API Key":
            return [], "请先在 AMAP_KEY 中填入高德地图 Web 服务 API Key"

        all_pois = []
        messages = []
        for page in range(max_pages):
            url = "https://restapi.amap.com/v3/place/text"
            params = {
                "key": self.api_key,
                "keywords": keyword,
                "city": city,
                "offset": offset,
                "page": page + 1,
                "extensions": "all",
                "show_fields": "business,photos,photos_info,children,shopinfo",
            }
            try:
                resp = requests.get(url, params=params, timeout=10)
                resp.raise_for_status()
                messages.append(self._check_amap_quota(resp))

                data = resp.json()
                for p in data.get("pois", []):
                    name = p.get("name", "")
                    address = p.get("address", "")
                    ptype = p.get("type", "")

                    biz_ext = p.get("biz_ext")
                    if isinstance(biz_ext, dict):
                        rating = biz_ext.get("rating")
                        cost = biz_ext.get("cost")
                    else:
                        rating = None
                        cost = None

                    tel = p.get("tel", "")
                    business_area = p.get("business_area", "")

                    poi_info = {"名称": name, "地址": address, "类型": ptype}
                    location = p.get("location", "")
                    if location:
                        poi_info["坐标"] = location
                    if rating:
                        poi_info["评分"] = rating
                    if cost:
                        poi_info["人均"] = f"{cost}元"
                    if tel:
                        poi_info["电话"] = tel
                    if business_area:
                        poi_info["所在区域"] = business_area

                    all_pois.append(poi_info)
            except Exception as e:
                messages.append(f"搜索出错：{e}")
                break
        return all_pois, " | ".join(messages)


class WeatherService:
    """负责拉取城市天气（数据来自 open-meteo，无需 Key）。"""

    @staticmethod
    def _weather_text(code):
        if code is None:
            return "未知"
        try:
            code = int(code)
        except (ValueError, TypeError):
            return "未知"

        table = {
            0: "晴", 1: "少云", 2: "多云", 3: "阴",
            45: "雾", 48: "冻雾",
            51: "小毛毛雨", 53: "中毛毛雨", 55: "大毛毛雨",
            56: "小冻毛毛雨", 57: "大冻毛毛雨",
            61: "小雨", 63: "中雨", 65: "大雨",
            66: "小冻雨", 67: "大冻雨",
            71: "小雪", 73: "中雪", 75: "大雪", 77: "冰粒",
            80: "小阵雨", 81: "中阵雨", 82: "大阵雨",
            85: "小阵雪", 86: "大阵雪",
            95: "雷暴", 96: "雷暴伴小冰雹", 99: "雷暴伴大冰雹",
        }
        return table.get(code, "未知")

    def fetch_weather(self, city, days=3):
        geo_url = "https://geocoding-api.open-meteo.com/v1/search"
        try:
            resp = requests.get(
                geo_url,
                params={"name": city, "count": 1, "language": "zh"},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json().get("results")
            if not results:
                return None
            loc = results[0]
            lat = loc.get("latitude")
            lon = loc.get("longitude")
            city_name = loc.get("name", city)
        except Exception:
            return None

        try:
            wurl = "https://api.open-meteo.com/v1/forecast"
            wresp = requests.get(
                wurl,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "temperature_2m_max,temperature_2m_min,weathercode",
                    "timezone": "auto",
                    "forecast_days": days,
                },
                timeout=10,
            )
            wresp.raise_for_status()
            daily = wresp.json().get("daily", {})
            dates = daily.get("time", [])
            tmax = daily.get("temperature_2m_max", [])
            tmin = daily.get("temperature_2m_min", [])
            codes = daily.get("weathercode", [])

            forecasts = []
            for i in range(len(dates)):
                forecasts.append({
                    "日期": dates[i],
                    "最高温": f"{tmax[i]}℃" if i < len(tmax) else "",
                    "最低温": f"{tmin[i]}℃" if i < len(tmin) else "",
                    "天气代码": codes[i] if i < len(codes) else None,
                })
            return {
                "城市": city_name,
                "查询时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "预报": forecasts,
            }
        except Exception:
            return None


class TravelGuide:
    """把上面的服务组合起来：生成、保存、读取攻略。"""

    def __init__(self, api_key):
        if requests is None:
            raise RuntimeError("请先安装 requests：pip install requests")
        self.poi = POIService(api_key)
        self.weather = WeatherService()

    # ---------- 文件读写（原本散落的函数，收进类里）----------
    def _ensure_dirs(self):
        if not os.path.exists(GUIDE_DIR):
            os.makedirs(GUIDE_DIR)

    def _save_json(self, path, data):
        self._ensure_dirs()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_json(self, path, default=None):
        if default is None:
            default = {}
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return default

    def load_usage(self):
        return self._load_json(USAGE_FILE, {"cities": []})

    def save_guide(self, city, guide):
        path = os.path.join(GUIDE_DIR, f"{city}.json")
        self._save_json(path, guide)

    def load_guide(self, city):
        path = os.path.join(GUIDE_DIR, f"{city}.json")
        return self._load_json(path, None)

    # ---------- 核心：生成一份攻略 ----------
    def build_guide(self, city, max_pois=5, weather_days=3):
        scenic_pois, m1 = self.poi.fetch_top_pois(city, "景点", max_pages=2, offset=10)
        food_pois, m2 = self.poi.fetch_top_pois(city, "美食", max_pages=2, offset=10)
        hotel_pois, m3 = self.poi.fetch_top_pois(city, "酒店", max_pages=2, offset=10)
        weather = self.weather.fetch_weather(city, days=weather_days)

        data = self.load_usage()
        if city not in data.get("cities", []):
            data.setdefault("cities", []).append(city)
            self._save_json(USAGE_FILE, data)

        guide = {
            "城市": city,
            "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "热门景点": [p for p in scenic_pois[:max_pois] if p.get("名称")],
            "美食推荐": [p for p in food_pois[:max_pois] if p.get("名称")],
            "住宿推荐": [p for p in hotel_pois[:max_pois] if p.get("名称")],
            "天气信息": weather,
            "提示": [m for m in (m1, m2, m3) if m],
        }
        self.save_guide(city, guide)
        return guide

    # ---------- 生成高德静态地图 URL（单类别，数字标签，自动缩放）----------
    @staticmethod
    def _fit_zoom(min_lng, max_lng, min_lat, max_lat, w=600, h=400, pad=0.72):
        """按所有点的包围盒，算出“刚好装下、且尽量放大”的缩放级别。
        放大到最大能把坐标相近的点拉开（减少标注互相覆盖），pad 留白保证边缘点不被裁掉。
        换算基于 Web 墨卡托：经度 像素跨度 = 度跨度 * 256*2^zoom/360；
        纬度因墨卡托拉伸需乘 sec(纬度)。"""
        import math
        span_lng = max(max_lng - min_lng, 1e-6)
        span_lat = max(max_lat - min_lat, 1e-6)
        lat_c = math.radians((min_lat + max_lat) / 2.0)
        sec = 1.0 / max(math.cos(lat_c), 1e-6)
        z_lng = math.log2(pad * w * 360.0 / (256.0 * span_lng))
        z_lat = math.log2(pad * h * 180.0 / (128.0 * span_lat * sec))
        z = int(math.floor(min(z_lng, z_lat)))
        return max(3, min(z, 16))

    # ---------- 抓图 & 配色小工具 ----------
    @staticmethod
    def _hex_to_rgb(color_hex):
        """'0xFF3030' / '#ff3030' -> (255,48,48)。解析失败给个红色兜底。"""
        s = str(color_hex).lower().replace("0x", "").replace("#", "").strip()
        try:
            v = int(s, 16)
            if len(s) >= 6:
                return (v >> 16) & 255, (v >> 8) & 255, v & 255
        except (ValueError, TypeError):
            pass
        return (200, 30, 30)

    def _fetch_bytes(self, url, timeout=12, retries=3):
        """拉取 PNG 二进制（带重试退避，抗高德瞬时限流）。失败返回 None。"""
        import time
        for attempt in range(retries):
            try:
                r = requests.get(url, timeout=timeout)
                if r.status_code == 200 and r.content[:4] == b"\x89PNG":
                    return r.content
            except Exception:
                pass
            if attempt < retries - 1:
                time.sleep(0.4 * (attempt + 1))
        return None

    # ---------- 自己画标注的静态地图（稳定，不受高德 markers 渲染影响）----------
    def render_map_b64(self, pois, color_hex, w=600, h=400):
        """先抓一张无标注底图，再用 PIL 按经纬度投影把序号亲自画上去。
        这样每个点都由我们绘制，彻底摆脱“重复 markers 参数只渲染一个点 / 服务端
        解码不一致漏标”的问题；若两点投影后过近，会自动错开，保证序号都看得见。
        返回 data URI 字符串；失败返回 None。"""
        import io
        import math
        from PIL import Image, ImageDraw, ImageFont

        coords = []
        for p in pois:
            loc = p.get("坐标")
            if not loc:
                continue
            try:
                lng, lat = (float(x) for x in str(loc).split(","))
            except (ValueError, TypeError):
                continue
            coords.append((lng, lat))
        if not coords:
            return None

        lngs = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        clng = (min(lngs) + max(lngs)) / 2.0
        clat = (min(lats) + max(lats)) / 2.0
        zoom = self._fit_zoom(min(lngs), max(lngs), min(lats), max(lats), w, h)

        url = (
            "https://restapi.amap.com/v3/staticmap"
            f"?key={self.poi.api_key}"
            f"&location={clng:.6f},{clat:.6f}"
            f"&zoom={zoom}&size={w}*{h}&format=png"
        )
        raw = self._fetch_bytes(url)
        if not raw:
            return None

        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        W, H = img.size

        # Web 墨卡托投影：把经纬度换算到与世界尺寸无关的“世界像素”，再平移到画布
        world = 256.0 * (2 ** zoom)

        def project(lng, lat):
            x = (lng + 180.0) / 360.0 * world
            s = math.sin(math.radians(lat))
            y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * world
            return x, y

        cx, cy = project(clng, clat)
        color = self._hex_to_rgb(color_hex)
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arialbd.ttf", 18)
        except Exception:
            font = ImageFont.load_default()

        placed = []  # 已放置的像素坐标，用来做重叠规避
        for idx, (lng, lat) in enumerate(coords, start=1):
            px, py = project(lng, lat)
            x = W / 2 + (px - cx)
            y = H / 2 + (py - cy)

            # 与已有标注太近则沿螺旋轻微外移，避免叠在一起看不清
            ox, oy = x, y
            step = 0
            while any(abs(ox - a) < 30 and abs(oy - b) < 26 for a, b in placed) and step < 14:
                step += 1
                ang = step * (math.pi / 3)
                rad = 8 + step * 5
                ox = x + math.cos(ang) * rad
                oy = y + math.sin(ang) * rad
            placed.append((ox, oy))

            if (round(ox), round(oy)) != (round(x), round(y)):
                draw.line([(x, y), (ox, oy)], fill=color + (170,), width=2)
                draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=color + (255,))

            r = 13
            draw.ellipse([ox - r, oy - r, ox + r, oy + r],
                         fill=color + (235,), outline=(255, 255, 255, 255), width=2)
            text = str(idx) if idx <= 9 else "A"
            tb = draw.textbbox((0, 0), text, font=font)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            draw.text((ox - tw / 2 - tb[0], oy - th / 2 - tb[1]),
                      text, font=font, fill=(255, 255, 255, 255))

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    # ---------- 城市背景图 ----------
    @staticmethod
    def geocode_city(city):
        """用 open-meteo 地理编码拿到城市中心经纬度（国内网络可达）。"""
        if not requests:
            return None
        try:
            r = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "zh"},
                timeout=10,
            )
            r.raise_for_status()
            res = r.json().get("results")
            if not res:
                return None
            return res[0].get("latitude"), res[0].get("longitude")
        except Exception:
            return None

    @staticmethod
    def local_bg_path(city):
        """若项目 city_bg/ 下存在该城市的图片，返回绝对路径，否则 None。
        支持按城市名直接命名，如 北京.jpg / 上海.png。"""
        if not os.path.isdir(CITY_BG_DIR):
            return None
        for ext in ("jpg", "jpeg", "png", "webp", "bmp"):
            p = os.path.join(CITY_BG_DIR, f"{city}.{ext}")
            if os.path.isfile(p):
                return p
        return None

    def build_map_bg_url(self, city):
        """没有本地图片时，用高德静态地图生成一张该城市的地理快照当背景。"""
        geo = self.geocode_city(city)
        if not geo:
            return None
        lat, lng = geo
        base = "https://restapi.amap.com/v3/staticmap"
        common = (
            f"key={self.poi.api_key}"
            f"&location={lng:.6f},{lat:.6f}"
            f"&zoom=11&size=1200*800"
        )
        return f"{base}?{common}"


class GuideApp:
    """Flet 桌面界面：负责把 TravelGuide 的结果漂亮地展示出来。"""

    POI_FIELDS = [
        ("所在区域", "区域："),
        ("地址", "地址："),
        ("评分", "评分："),
        ("人均", "人均："),
        ("电话", "电话："),
    ]

    # 三个板块 + 对应的标注颜色（红=景点 / 绿=美食 / 蓝=住宿）
    SECTIONS = [
        ("热门景点", "0xFF3030"),
        ("美食推荐", "0x20A020"),
        ("住宿推荐", "0x3060FF"),
    ]

    def __init__(self, page: ft.Page):
        self.page = page
        self.guide = TravelGuide(AMAP_KEY)
        self._build_ui()

    # ---------------- 界面搭建 ----------------
    def _build_ui(self):
        self.page.title = "旅游攻略生成器"
        self.page.theme = ft.Theme(color_scheme_seed=ft.Colors.INDIGO)
        self.page.padding = 20
        self.page.window.width = 1000
        self.page.window.height = 760
        self.page.window.resizable = True
        self.page.bgcolor = ft.Colors.BLACK  # 待输入时的纯黑底；生成攻略、拿到城市背景图后再叠加
        self.page.theme_mode = ft.ThemeMode.DARK  # 深色主题：文本框/下拉框/卡片统一深底白字，不再灰蒙蒙

        # 背景三层：城市图片（铺满）+ 半透明黑遮罩（压暗背景，保证文字清晰）+ 内容层
        # 待输入（还没有背景图）时遮罩隐藏、保持纯黑；拿到背景图后再显示遮罩。
        # 注意：没有背景图时不要创建 ft.Image（src=None 会触发
        # “Image must have 'src' specified.” 报错），改成用到时才懒加载。
        self.bg_image = None
        self.bg_overlay = ft.Container(
            expand=True,
            visible=False,
            bgcolor=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
        )

        self.city_input = ft.TextField(label="城市名称", width=200, hint_text="如：北京")
        gen_btn = ft.FilledButton(
            content="生成攻略",
            icon=ft.Icons.SEARCH,
            on_click=self._on_generate,
            style=ft.ButtonStyle(color=ft.Colors.WHITE, bgcolor=ft.Colors.INDIGO),
        )

        self.loading = ft.Row(
            [ft.ProgressRing(width=18, height=18), ft.Text("正在生成，请稍候…")],
            visible=False,
        )

        self.history_dd = ft.Dropdown(
            label="查看历史攻略", width=240, options=[], on_select=self._on_history_select,
        )
        refresh_btn = ft.FilledButton(content="刷新历史", on_click=self._refresh_history)

        self.results = ft.Column(spacing=0)

        # 深色面板底色：在纯黑页面上看起来就是纯黑；生成背景图后也只透出很淡的城市轮廓，
        # 既保持“纯黑”观感，又不会遮挡文字。
        panel_bg = ft.Colors.with_opacity(0.8, ft.Colors.BLACK)

        # 内容层：顶部标题/输入框等放在深色面板里，确保任何背景下都清晰可读
        self.content = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        self.content.controls.extend([
            ft.Container(
                content=ft.Column([
                    ft.Text("🧳 旅游攻略生成器", size=26, weight=ft.FontWeight.BOLD),
                    ft.Text("输入城市，一键生成景点 / 美食 / 住宿 / 天气攻略", size=13, color=ft.Colors.GREY),
                ], spacing=2),
                bgcolor=panel_bg,
                padding=12, border_radius=10,
            ),
            ft.Divider(height=2),
            ft.Container(
                content=ft.Row([self.city_input, gen_btn],
                               alignment=ft.MainAxisAlignment.START, spacing=10),
                bgcolor=panel_bg,
                padding=12, border_radius=10,
            ),
            self.loading,
            ft.Divider(height=2),
            ft.Container(
                content=ft.Row([self.history_dd, refresh_btn], spacing=10),
                bgcolor=panel_bg,
                padding=12, border_radius=10,
            ),
            self.results,
        ])

        # 背景图是懒加载的：只有真正拿到城市背景时才 insert 到栈底，避免空 src 报错
        self.stack = ft.Stack(expand=True, controls=[self.bg_overlay, self.content])
        self.page.add(self.stack)
        self._refresh_history()

    # ---------------- 事件处理 ----------------
    async def _on_generate(self, e):
        city = self.city_input.value.strip()
        if not city:
            self._notify("城市名不能为空")
            return

        self.loading.visible = True
        self.results.controls.clear()
        self.page.update()

        try:
            guide = await asyncio.to_thread(self.guide.build_guide, city)
            maps = await asyncio.to_thread(self._build_maps, guide)
            self._render_guide(guide, maps)
            self._set_background(city)
        except Exception as ex:
            self._notify(f"生成失败：{ex}")
        finally:
            self.loading.visible = False
            self.page.update()

    def _on_history_select(self, e):
        city = self.history_dd.value
        if not city:
            return
        guide = self.guide.load_guide(city)
        if guide:
            maps = self._build_maps(guide)
            self._render_guide(guide, maps)
            self._set_background(city)
        else:
            self._notify("攻略文件不存在")

    def _refresh_history(self, e=None):
        data = self.guide.load_usage()
        cities = data.get("cities", [])
        self.history_dd.options = [ft.dropdown.Option(c) for c in cities]
        self.page.update()

    def _set_background(self, city):
        """设置整页城市背景：优先用 city_bg/ 里的本地图片，否则用高德地图快照。
        半透明黑遮罩已在 _build_ui 铺好，保证任何背景下文字都清晰、不被遮挡。"""
        src = None
        local = TravelGuide.local_bg_path(city)
        if local:
            try:
                with open(local, "rb") as f:
                    data = f.read()
                ext = os.path.splitext(local)[1].lower().lstrip(".")
                mime = {
                    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                    "webp": "image/webp", "bmp": "image/bmp",
                }.get(ext, "image/jpeg")
                src = f"data:{mime};base64,{base64.b64encode(data).decode()}"
            except Exception:
                src = None
        else:
            # 没有本地图：用高德静态地图生成该城市的地理快照当背景（Python 先抓图，更稳）
            src = self._fetch_image_b64(self.guide.build_map_bg_url(city))
        self._apply_background(src)

    def _apply_background(self, src):
        """把背景图放到栈底（懒创建）；拿不到图就保持纯黑，连遮罩也不显示。"""
        if not src:
            # 没图：保持纯黑，绝不出现灰底
            if self.bg_image is not None:
                self.bg_image.visible = False
            self.bg_overlay.visible = False
        else:
            if self.bg_image is None:
                self.bg_image = ft.Image(src=src, expand=True, fit="cover")
                self.stack.controls.insert(0, self.bg_image)
            else:
                self.bg_image.src = src
                self.bg_image.visible = True
            self.bg_overlay.visible = True
        try:
            self.page.update()
        except Exception:
            pass

    def _fetch_image_b64(self, url, timeout=12, retries=3):
        """用 Python（requests）拉取图片字节并转成 base64 data URI 再交给 Flet 显示。
        高德静态地图偶有瞬时限流（返回错误 JSON 而非 PNG），这里加重试 + 退避，
        显著减少“地图加载失败 / 标注不全”这类不稳定现象。返回 data URI；全失败返回 None。"""
        import time
        for attempt in range(retries):
            try:
                r = requests.get(url, timeout=timeout)
                # 成功时是 PNG（文件头 89 50 4E 47）；限流/报错时是 JSON，直接重试
                if r.status_code == 200 and r.content[:4] == b"\x89PNG":
                    return f"data:image/png;base64,{base64.b64encode(r.content).decode()}"
            except Exception:
                pass
            if attempt < retries - 1:
                time.sleep(0.4 * (attempt + 1))  # 退避：0.4s、0.8s
        return None

    def _notify(self, msg):
        self.page.snack_bar = ft.SnackBar(ft.Text(msg))
        self.page.snack_bar.open = True
        self.page.update()

    # ---------------- 结果渲染 ----------------
    def _build_maps(self, guide):
        """为三个板块各自渲染一张带序号标注的地图，返回 {板块名: data-uri}。
        纯网络 + PIL 计算，放在后台线程里跑，不卡界面。"""
        maps = {}
        for title, color in self.SECTIONS:
            maps[title] = self.guide.render_map_b64(guide.get(title, []), color)
        return maps

    def _render_guide(self, guide, maps=None):
        maps = maps or {}
        self.results.controls.clear()

        self.results.controls.append(
            ft.Text(f"{guide['城市']} 旅游攻略", size=22, weight=ft.FontWeight.BOLD)
        )
        self.results.controls.append(
            ft.Text(f"更新时间：{guide['更新时间']}", size=12, color=ft.Colors.GREY)
        )

        for title, _color in self.SECTIONS:
            pois = guide.get(title, [])
            card = self._section_card(title, pois, numbered=True)
            row = ft.Row(
                [card, self._map_card(maps.get(title), title)],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.START,
                spacing=12,
            )
            self.results.controls.append(row)

        self.results.controls.append(self._weather_card(guide.get("天气信息")))

        tips = NATIONAL_TIPS.get(guide.get("城市", ""))
        if tips:
            self.results.controls.append(self._tips_card(tips))

        msgs = guide.get("提示")
        if msgs:
            self.results.controls.append(
                ft.Text("ℹ️ " + " | ".join(msgs), size=11, color=ft.Colors.BLUE)
            )

        self.page.update()

    def _map_card(self, src, caption):
        if src:
            img = ft.Image(src=src, width=400, height=300, fit="contain")
        else:
            img = ft.Container(
                content=ft.Text(
                    "地图加载失败（高德接口暂时不可用，可稍后重试）",
                    color=ft.Colors.GREY,
                    size=12,
                ),
                width=400,
                height=300,
                alignment=ft.alignment.center,
            )
        return ft.Card(
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(f"{caption}位置", size=14, weight=ft.FontWeight.BOLD),
                        img,
                        ft.Text("数字标记对应左侧序号", size=11, color=ft.Colors.GREY),
                    ],
                    spacing=4,
                ),
                padding=12,
            ),
            elevation=3,
            margin=ft.Margin(left=0, top=0, right=0, bottom=12),
        )

    def _section_card(self, title, pois, numbered=False):
        children = [
            ft.Text(title, size=16, weight=ft.FontWeight.BOLD),
            ft.Divider(height=1),
        ]
        if not pois:
            children.append(ft.Text("暂无数据", color=ft.Colors.GREY, size=13))
        for i, p in enumerate(pois, start=1):
            sub = []
            for key, label in self.POI_FIELDS:
                v = p.get(key)
                if v:
                    sub.append(f"{label}{v}")
            name = f"{i}. {p.get('名称', '')}" if numbered else p.get("名称", "")
            children.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(name, size=14, weight=ft.FontWeight.W_600),
                            ft.Text("   ".join(sub), size=12, color=ft.Colors.GREY) if sub
                            else ft.Text("", size=1),
                        ]
                    ),
                    padding=ft.Padding(left=0, top=0, right=0, bottom=8),
                )
            )
        return ft.Card(
            content=ft.Container(content=ft.Column(children, tight=True, spacing=4), padding=15),
            elevation=3,
            margin=ft.Margin(left=0, top=0, right=0, bottom=12),
            width=380,
        )

    def _weather_card(self, weather):
        children = [
            ft.Text("出行天气", size=16, weight=ft.FontWeight.BOLD),
            ft.Divider(height=1),
        ]
        if not weather:
            children.append(ft.Text("天气信息暂不可用", color=ft.Colors.GREY))
        else:
            children.append(
                ft.Text(f"城市：{weather['城市']}    查询时间：{weather['查询时间']}", size=12)
            )
            for item in weather.get("预报", []):
                text = WeatherService._weather_text(item.get("天气代码"))
                children.append(
                    ft.Text(
                        f"{item.get('日期', '')}  {text}  "
                        f"{item.get('最低温', '')} ~ {item.get('最高温', '')}",
                        size=13,
                    )
                )
        return ft.Card(
            content=ft.Container(content=ft.Column(children, spacing=4), padding=15),
            elevation=3,
            margin=ft.Margin(left=0, top=0, right=0, bottom=12),
        )

    def _tips_card(self, tips):
        children = [ft.Text("出行提示", size=16, weight=ft.FontWeight.BOLD), ft.Divider(height=1)]
        for t in tips:
            children.append(ft.Text(f"• {t}", size=13))
        return ft.Card(
            content=ft.Container(content=ft.Column(children), padding=15),
            elevation=3,
            margin=ft.Margin(left=0, top=0, right=0, bottom=12),
        )


def main(page: ft.Page):
    GuideApp(page)


if __name__ == "__main__":
    ft.run(main)
