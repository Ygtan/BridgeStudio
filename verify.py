"""求解器校验：用简支梁理论解比对 anastruct 数值。

理论（简支梁，均布荷载 w，跨度 L）：
  跨中弯矩 M = w L^2 / 8
  跨中挠度 f = 5 w L^4 / (384 E I)
  支座反力 R = w L / 2
"""

from model_spec import ModelSpec, Section
from fe_solver import solve


def main():
    spec = ModelSpec(
        name="校验模型",
        length=10.0,
        n_spans=1,
        section=Section(shape="rectangular", h=1.0, b=0.5),
        material_name="钢材 Q345",
        E=2.06e11,
        rho=7850.0,
        supports=[{"position": 0.0, "type": "pinned"}, {"position": 10.0, "type": "pinned"}],
        loads=[{"type": "udl", "value": 20.0, "start": None, "end": None}],
    )
    res = solve(spec)

    w = 20e3
    L = 10.0
    E = 2.06e11
    I = res["props"]["I"]
    M_th = w * L**2 / 8
    f_th = 5 * w * L**4 / (384 * E * I)
    R_th = w * L / 2

    print(f"截面 I = {I:.6e} m^4")
    print(f"理论 跨中弯矩 = {M_th/1000:.2f} kN·m | 数值 = {res['max_moment']/1000:.2f} kN·m")
    print(f"理论 跨中挠度 = {f_th*1000:.3f} mm | 数值 = {res['max_deflection']*1000:.3f} mm")
    sumR = sum(abs(v.get("Fy", 0)) for v in res["reactions"].values())
    print(f"理论 反力合计 = {R_th/1000*2:.2f} kN | 数值 = {sumR/1000:.2f} kN")
    print("反力明细:", {k: round(v["Fy"], 1) for k, v in res["reactions"].items()})
    print("弯矩范围: min", round(min(res["moments"]), 1), "max", round(max(res["moments"]), 1), "N·m")


if __name__ == "__main__":
    main()
