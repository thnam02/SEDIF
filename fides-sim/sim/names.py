"""Tên người Việt và chuyển tên sang ASCII in hoa."""

import unicodedata

import numpy as np

# Họ phổ biến. Bốn họ đầu theo tỷ lệ đã chốt; phần còn lại cộng đủ 1.
SURNAMES: list[tuple[str, float]] = [
    ("Nguyễn", 0.380),
    ("Trần", 0.110),
    ("Lê", 0.095),
    ("Phạm", 0.070),
    ("Hoàng", 0.050),
    ("Huỳnh", 0.050),
    ("Phan", 0.045),
    ("Vũ", 0.035),
    ("Võ", 0.035),
    ("Đặng", 0.025),
    ("Bùi", 0.020),
    ("Đỗ", 0.015),
    ("Hồ", 0.012),
    ("Ngô", 0.012),
    ("Dương", 0.010),
    ("Lý", 0.008),
    ("Đinh", 0.007),
    ("Mai", 0.005),
    ("Trương", 0.005),
    ("Lâm", 0.004),
    ("Đào", 0.003),
    ("Tạ", 0.002),
    ("Cao", 0.002),
]

# Tên đệm đứng đầu là Văn/Thị, phổ biến hơn ở người lớn tuổi.
MALE_MIDDLE = np.array(
    [
        "Văn", "Đức", "Minh", "Quang", "Hữu", "Thanh", "Công", "Hoàng", "Gia", "Anh",
        "Tuấn", "Quốc", "Trọng", "Xuân", "Bảo", "Đình", "Mạnh", "Duy", "Tiến", "Ngọc",
        "Thế", "Hải", "Trung", "Nhật", "Phúc",
    ]
)
FEMALE_MIDDLE = np.array(
    [
        "Thị", "Ngọc", "Thu", "Thanh", "Minh", "Phương", "Hồng", "Kim", "Bảo", "Mai",
        "Thùy", "Diệu", "Khánh", "Hải", "Cẩm", "Bích", "Quỳnh", "Tuyết", "Hoài", "Mỹ",
        "Như", "Lan", "Yến", "Hà", "Gia",
    ]
)
# Tên gọi: vài tên đầu phổ biến, đuôi là tên hiếm.
MALE_GIVEN = np.array(
    [
        "An", "Bình", "Cường", "Dũng", "Hải", "Huy", "Khoa", "Long", "Nam", "Phong",
        "Quân", "Sơn", "Tuấn", "Việt", "Đạt", "Đức", "Khang", "Phúc", "Thịnh", "Vũ",
        "Hoàng", "Minh", "Trí", "Lâm", "Nhân", "Phát", "Tâm", "Kiên", "Hưng", "Đăng",
        "Anh", "Bảo", "Duy", "Hiếu", "Hùng", "Khánh", "Khôi", "Lộc", "Mạnh", "Nghĩa",
        "Nhật", "Phú", "Quang", "Quốc", "Tài", "Thắng", "Thành", "Thiên", "Toàn", "Trung",
        "Tú", "Tùng", "Vinh", "Xuân", "Bách", "Cảnh", "Chương", "Đại", "Điền", "Định",
        "Đoàn", "Đồng", "Hà", "Hào", "Hậu", "Hiển", "Hiệp", "Hòa", "Hoàn", "Hữu",
        "Kha", "Khiêm", "Khương", "Kiệt", "Lân", "Lập", "Lễ", "Liêm", "Luân", "Lương",
        "Nghị", "Nguyên", "Nhã", "Ninh", "Phi", "Quý", "Quyền", "Sáng", "Siêu", "Tấn",
        "Thái", "Thiện", "Thọ", "Thuận", "Tiến", "Tín", "Trọng", "Trường", "Tuệ", "Tường",
        "Vạn", "Văn", "Vĩ", "Vĩnh", "Vỹ", "Ân", "Bằng", "Cát", "Chấn", "Chiến",
        "Chính", "Công", "Cương", "Danh", "Đạo", "Đình", "Đông", "Gia", "Giang", "Giáp",
        "Hạ", "Hạc", "Hành", "Hạo", "Hiền", "Khải", "Khắc", "Khoan", "Kỳ", "Lạc",
        "Lịch", "Lợi", "Mẫn", "Năng", "Nghiêm", "Nghiệp", "Ngọ",         "Phàm", "Phấn", "Phước", "Quảng", "Sâm", "Sĩ", "Tạc", "Thạch", "Thông", "Thuyết",
        "Tịch", "Tuân", "Túc",
    ]
)
FEMALE_GIVEN = np.array(
    [
        "An", "Anh", "Ánh", "Bình", "Chi", "Châu", "Diệu", "Dung", "Duyên", "Giang",
        "Hà", "Hạnh", "Hằng", "Hiền", "Hoa", "Huệ", "Hương", "Huyền", "Khánh", "Kiều",
        "Lam", "Lan", "Linh", "Liên", "Loan", "Ly", "Mai", "My", "Nga", "Ngân",
        "Ngọc", "Nhi", "Như", "Nhung", "Oanh", "Phương", "Quyên", "Quỳnh", "Sen", "Thảo",
        "Thư", "Thủy", "Tiên", "Trà", "Trâm", "Trang", "Trinh", "Trúc", "Tuyết", "Uyên",
        "Vân", "Vi", "Vy", "Xuân", "Yến", "Ái", "Đan", "Diễm", "Hạ", "Hân",
        "Hồng", "Khuê", "Kim", "Lệ", "Na", "Nhiên", "Phụng", "Quế", "Sa", "Sương",
        "Thanh", "Thắm", "Thi", "Thoại", "Thu", "Thúy", "Thương", "Tình", "Tố", "Trân",
        "Tú", "Tuyền", "Uyển", "Yên", "Bích", "Cẩm", "Đào", "Gấm", "Hiếu", "Lê",
        "Luyến", "Mẫn", "Xuyến", "Băng", "Cúc", "Diên", "Diệp", "Đoan", "Đông", "Hảo",
        "Hiên", "Hoài", "Huỳnh", "Khanh", "Khiết", "Khôi", "Khuyên", "Lành", "Liễu", "Lụa",
        "Lưu", "Mơ", "Ngà", "Nghĩa", "Nghi", "Nguyệt", "Nhàn", "Nương", "Phi", "Phúc",
        "San", "Tâm", "Thêu", "Thoa", "Thơm", "Thuần", "Thục", "Trì", "Tuệ", "Vàng",
        "Vui", "Xinh", "Dao", "Cầm", "Chúc", "Giao", "Kỳ", "Mận", "Mỹ", "Nhã",
        "Nở", "Nụ", "Bội", "Cát", "Dạ", "Lài", "Nết", "Phấn", "Tịnh", "Ý",
        "Quyết", "Rạng", "Viện", "Yểu", "Ẩn", "Phượng", "Bống", "Chăm", "Hợi", "Thơ",
    ]
)

EXTENDED_SHARE = 0.15
GIVEN_ALPHA = 0.18
MIDDLE_ALPHA = 0.12
# Người sinh sau 1995 ít dùng tên đệm Văn/Thị hơn.
YOUNG_HEAD = 0.28
OLD_HEAD = 2.4


def strip_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt. Đ/đ thành D/d vì chữ Đ không tách được bằng NFD."""
    text = text.replace("Đ", "D").replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def to_name_ascii(full_name: str) -> str:
    """Tên in hoa, không dấu, Đ → D."""
    return strip_diacritics(full_name).upper()


def surname_table() -> tuple[np.ndarray, np.ndarray]:
    names = np.array([name for name, _ in SURNAMES])
    weights = np.array([weight for _, weight in SURNAMES], dtype=float)
    weights = weights / weights.sum()
    return names, weights


def _skew(n_options: int, alpha: float, head_scale: float) -> np.ndarray:
    weights = np.arange(1, n_options + 1, dtype=float) ** (-alpha)
    weights[0] *= head_scale
    return weights / weights.sum()


def _choice(rng: np.random.Generator, options: np.ndarray, weights: np.ndarray, size: int) -> np.ndarray:
    if size == 0:
        return np.array([], dtype=object)
    return rng.choice(options, size=size, p=weights)


def _fill_middle(rng, options: np.ndarray, young_w: np.ndarray, old_w: np.ndarray, mask: np.ndarray, young: np.ndarray, into: np.ndarray) -> None:
    chosen = np.flatnonzero(mask)
    if len(chosen) == 0:
        return
    is_young = young[chosen]
    young_idx = chosen[is_young]
    old_idx = chosen[~is_young]
    into[young_idx] = _choice(rng, options, young_w, len(young_idx))
    into[old_idx] = _choice(rng, options, old_w, len(old_idx))


def sample_name_body(rng: np.random.Generator, male: np.ndarray, young: np.ndarray) -> np.ndarray:
    """Phần tên sau họ. 15% có tên đệm hai chữ hoặc tên ghép. `young` là sinh sau 1995."""
    n = len(male)
    male_given_w = _skew(len(MALE_GIVEN), GIVEN_ALPHA, 1.0)
    female_given_w = _skew(len(FEMALE_GIVEN), GIVEN_ALPHA, 1.0)
    male_young_w = _skew(len(MALE_MIDDLE), MIDDLE_ALPHA, YOUNG_HEAD)
    male_old_w = _skew(len(MALE_MIDDLE), MIDDLE_ALPHA, OLD_HEAD)
    female_young_w = _skew(len(FEMALE_MIDDLE), MIDDLE_ALPHA, YOUNG_HEAD)
    female_old_w = _skew(len(FEMALE_MIDDLE), MIDDLE_ALPHA, OLD_HEAD)

    middle = np.empty(n, dtype=object)
    given = np.empty(n, dtype=object)
    _fill_middle(rng, MALE_MIDDLE, male_young_w, male_old_w, male, young, middle)
    _fill_middle(rng, FEMALE_MIDDLE, female_young_w, female_old_w, ~male, young, middle)
    n_male = int(male.sum())
    n_female = n - n_male
    if n_male:
        given[male] = _choice(rng, MALE_GIVEN, male_given_w, n_male)
    if n_female:
        given[~male] = _choice(rng, FEMALE_GIVEN, female_given_w, n_female)

    extended = rng.random(n) < EXTENDED_SHARE
    double_middle = extended & (rng.random(n) < 0.5)
    compound = extended & ~double_middle

    extra_middle = np.empty(n, dtype=object)
    _fill_middle(rng, MALE_MIDDLE, male_young_w, male_old_w, double_middle & male, young, extra_middle)
    _fill_middle(rng, FEMALE_MIDDLE, female_young_w, female_old_w, double_middle & ~male, young, extra_middle)
    same = double_middle & (extra_middle == middle)
    for _ in range(6):
        if not np.any(same):
            break
        redraw = np.empty(n, dtype=object)
        _fill_middle(rng, MALE_MIDDLE, male_young_w, male_old_w, same & male, young, redraw)
        _fill_middle(rng, FEMALE_MIDDLE, female_young_w, female_old_w, same & ~male, young, redraw)
        extra_middle[same] = redraw[same]
        same = double_middle & (extra_middle == middle)

    extra_given = np.empty(n, dtype=object)
    compound_male = np.flatnonzero(compound & male)
    compound_female = np.flatnonzero(compound & ~male)
    extra_given[compound_male] = _choice(rng, MALE_GIVEN, male_given_w, len(compound_male))
    extra_given[compound_female] = _choice(rng, FEMALE_GIVEN, female_given_w, len(compound_female))
    same_given = compound & (extra_given == given)
    for _ in range(6):
        if not np.any(same_given):
            break
        redraw_male = np.flatnonzero(same_given & male)
        redraw_female = np.flatnonzero(same_given & ~male)
        extra_given[redraw_male] = _choice(rng, MALE_GIVEN, male_given_w, len(redraw_male))
        extra_given[redraw_female] = _choice(rng, FEMALE_GIVEN, female_given_w, len(redraw_female))
        same_given = compound & (extra_given == given)

    body = np.char.add(np.char.add(middle.astype(str), " "), given.astype(str))
    if np.any(double_middle):
        doubled = np.char.add(
            np.char.add(np.char.add(middle.astype(str), " "), np.char.add(extra_middle.astype(str), " ")),
            given.astype(str),
        )
        body = np.where(double_middle, doubled, body)
    if np.any(compound):
        # Tên ghép không kèm tên đệm một chữ: "Trần Minh Thư".
        joined = np.char.add(np.char.add(given.astype(str), " "), extra_given.astype(str))
        body = np.where(compound, joined, body)
    return body
