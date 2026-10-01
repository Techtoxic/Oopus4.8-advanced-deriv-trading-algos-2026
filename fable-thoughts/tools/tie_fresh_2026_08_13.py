import numpy as np, math
d = np.load("/tmp/jd100_fresh.npz")
ep, px, pip = d["ep"], d["px"], int(d["pip"])
x = np.round(px * 10**pip).astype(np.int64)
gap = np.diff(ep); steps = np.diff(x)
m = gap == 1
s = steps[m]
n = len(s)
for durlbl, dur in [("1t",1),("2t",2),("3t",3)]:
    # k-tick move on consecutive runs
    ok = np.ones(len(x)-dur, bool)
    for j in range(dur):
        ok &= (ep[j+1:len(ep)-dur+j+1] - ep[j:len(ep)-dur+j]) == 1
    mv = x[dur:] - x[:-dur]
    mv = mv[ok]
    pt = np.mean(mv==0); pu = np.mean(mv>0); pd = np.mean(mv<0)
    nn = len(mv)
    se = math.sqrt(pt*(1-pt)/nn)
    print(f"dur {durlbl}: n={nn} P(up)={pu:.4f} P(tie)={pt:.4f}±{se:.4f} P(down)={pd:.4f}")
    for M in [1.835, 1.800, 1.762, 1.70]:
        ev = M*(1+pt) - 2
        print(f"    pair EV @ CALLE=PUTE={M}: {ev/2:+.3%} of total stake")
