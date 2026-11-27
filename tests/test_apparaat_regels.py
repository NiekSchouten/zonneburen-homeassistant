"""De veilige regels voor apparaten, zonder Home Assistant."""

import random

from custom_components.zonneburen import apparaat_regels as r

KAMER = r.Grenzen(14, 24, 0.5)
BOILER = r.Grenzen(30, 60, 1)


def test_verwarming_gaat_nooit_uit_zonder_toestemming():
    redenen = ["Energieplan", "Energieplan: pauze", "Energieplan: wacht op het beste moment", "Handmatig aan", "Handmatig uit",
               "Noodstop", "Wijkstroom", "Normaal aan", "Uit"]
    for reden in redenen:
        for aan in (True, False):
            for stand in ("", "aanraden", "blokkeren", "start"):
                assert r.thermische_wens(reden, aan, stand, mag_uit=False) != r.UITZETTEN


def test_uit_alleen_als_de_bewoner_het_toestaat():
    assert r.thermische_wens("Energieplan: pauze", False, "blokkeren", mag_uit=True) == r.UITZETTEN
    assert r.thermische_wens("Handmatig uit", False, mag_uit=True) == r.UITZETTEN
    # Buiten een blok ("wacht op het beste moment") nooit uit, ook niet met toestemming.
    assert r.thermische_wens("Energieplan: wacht op het beste moment", False, "", mag_uit=True) == r.NORMAAL


def test_warmtepomp_voorverwarmen_normaal_besparen():
    assert r.thermisch_doel("climate", r.VERHOGEN, 20.5, KAMER) == 21.5
    assert r.thermisch_doel("climate", r.NORMAAL, 20.5, KAMER) == 20.5
    assert r.thermisch_doel("climate", r.BESPAREN, 20.5, KAMER) == 20.0


def test_het_plan_mag_niet_verder_dan_de_verschuiving():
    # De server vraagt 17 graden (oude pauze) of 25: de uitvoerder houdt het bij -0,5 en +1.
    assert r.thermisch_doel("climate", r.BESPAREN, 20.5, KAMER, plan_temp=17) == 20.0
    assert r.thermisch_doel("climate", r.VERHOGEN, 20.5, KAMER, plan_temp=25) == 21.5
    # En binnen de comfortgrenzen 19-22.
    assert r.thermisch_doel("climate", r.VERHOGEN, 21.5, KAMER) == 22.0


def test_koelen_verschuift_de_andere_kant_op():
    koel = r.Grenzen(16, 30, 0.5, koelt=True)
    assert r.thermisch_doel("climate", r.VERHOGEN, 21, koel) == 20.0     # voorkoelen
    assert r.thermisch_doel("climate", r.BESPAREN, 21, koel) == 21.5


def test_boiler_warmer_maar_nooit_kouder():
    assert r.thermisch_doel("water_heater", r.VERHOGEN, 40, BOILER) == 50
    assert r.thermisch_doel("water_heater", r.NORMAAL, 40, BOILER) == 40
    assert r.thermisch_doel("water_heater", r.BESPAREN, 40, BOILER) == 40
    assert r.thermisch_doel("water_heater", r.VERHOGEN, 40, BOILER, plan_temp=60) == 60   # legionella
    assert r.thermisch_doel("water_heater", r.VERHOGEN, 40, r.Grenzen(30, 55, 1), plan_temp=60) == 55


def test_handmatig_pas_na_de_naijltijd_van_de_cloud():
    assert not r.is_handmatig(21.0, 21.5, 1000, 1000 + 60, 0.5)            # cloud loopt nog achter
    assert r.is_handmatig(21.0, 21.5, 1000, 1000 + r.NAIJLEN_S, 0.5)
    assert not r.is_handmatig(21.5, 21.5, 1000, 99999, 0.5)


def test_schrijflimiet_een_keer_per_half_uur_bij_een_cloud():
    lim = r.Schrijflimiet()
    assert lim.mag("climate.wp", 21.5, 0, r.CLOUD_S)
    lim.noteer("climate.wp", 21.5, 0)
    assert not lim.mag("climate.wp", 21.5, 10_000, r.CLOUD_S)              # niets veranderd
    assert not lim.mag("climate.wp", 20.5, 600, r.CLOUD_S)                 # binnen het half uur
    assert lim.mag("climate.wp", 20.5, r.CLOUD_S, r.CLOUD_S)


def test_stilstand_nooit_uit_terwijl_hij_draait():
    s = r.Stilstand()
    assert not s.mag_uit(0)                    # zonder meting nooit
    s.meet(1800, 100)
    assert not s.mag_uit(100) and s.draait(100)
    s.meet(2, 150)
    assert not s.mag_uit(150 + 60)             # nog geen twee minuten stil
    assert s.mag_uit(100 + r.STIL_S)


def test_vaatwasser_klaargezet_dan_aan_tot_klaar_en_daarna_aan():
    s = r.Stilstand()
    s.meet(0, 0)
    # Klaargezet: uit tot het blok.
    assert r.programma_wens(None, True, False, True, s, 10) is False
    assert r.programma_wens(True, True, False, False, s, 20) is True
    # Uiterlijke start zonder blok: toch aan, dan is hij op tijd klaar.
    assert r.programma_wens(None, True, False, False, s, 100, start_uiterlijk=90) is True
    # Loopt: altijd aan, ook als het blok voorbij is.
    assert r.programma_wens(False, False, True, True, s, 30) is True
    # Daarna: niets doen, de machine blijft gewoon aan.
    assert r.programma_wens(None, False, False, True, s, 10_000) is None
    s.meet(1800, 10_000)
    assert r.programma_wens(None, False, False, True, s, 10_001, uit_als_ongebruikt=True) is None


def test_eigenschap_nooit_buiten_de_comfortgrenzen():
    rnd = random.Random(7)
    for _ in range(2000):
        basis = rnd.choice([18, 19.5, 20, 20.5, 21, 22, 23])
        wens = rnd.choice([r.VERHOGEN, r.NORMAAL, r.BESPAREN])
        plan = rnd.choice([None, rnd.uniform(5, 30)])
        g = r.Grenzen(rnd.choice([5, 14, 16]), rnd.choice([24, 28, 30]), rnd.choice([0.5, 1]))
        doel = r.thermisch_doel("climate", wens, basis, g, plan)
        assert g.tmin <= doel <= g.tmax
        assert min(19, basis) - 0.5 <= doel <= max(22, basis) + 0.5
        assert basis - 0.5 - 0.5 <= doel <= basis + 1 + 0.5
