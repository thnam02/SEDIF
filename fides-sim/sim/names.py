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

MALE_MIDDLE = np.array(
    ["Văn", "Hữu", "Đức", "Minh", "Quang", "Công", "Đình", "Xuân", "Thành", "Ngọc", "Hoàng", "Gia", "Bảo", "Trọng", "Quốc"]
)
FEMALE_MIDDLE = np.array(
    ["Thị", "Ngọc", "Thu", "Hồng", "Thanh", "Phương", "Mai", "Lan", "Thùy", "Kim", "Diệu", "Mỹ", "Bảo", "Ánh", "Quỳnh"]
)
MALE_GIVEN = np.array(
    [
        "An", "Bình", "Cường", "Dũng", "Hải", "Huy", "Khoa", "Long", "Nam", "Phong",
        "Quân", "Sơn", "Tuấn", "Việt", "Đạt", "Đức", "Khang", "Phúc", "Thịnh", "Vũ",
        "Hoàng", "Minh", "Trí", "Lâm", "Nhân", "Phát", "Tâm", "Kiên", "Hưng", "Đăng",
    ]
)
FEMALE_GIVEN = np.array(
    [
        "Anh", "Chi", "Hà", "Hương", "Lan", "Linh", "Mai", "Nga", "Ngọc", "Phương",
        "Thảo", "Trang", "Vy", "Yến", "Ánh", "Châu", "Diệu", "Giang", "Hạnh", "Hiền",
        "Khánh", "Ly", "My", "Nhi", "Oanh", "Quỳnh", "Trinh", "Uyên", "Vân", "Xuân",
    ]
)


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


def sample_middle_given(rng: np.random.Generator, male: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Tên đệm và tên gọi theo giới tính. `male` là mảng bool."""
    n = len(male)
    middle = np.empty(n, dtype=object)
    given = np.empty(n, dtype=object)
    n_male = int(male.sum())
    n_female = n - n_male
    if n_male:
        middle[male] = rng.choice(MALE_MIDDLE, size=n_male)
        given[male] = rng.choice(MALE_GIVEN, size=n_male)
    if n_female:
        middle[~male] = rng.choice(FEMALE_MIDDLE, size=n_female)
        given[~male] = rng.choice(FEMALE_GIVEN, size=n_female)
    return middle, given
