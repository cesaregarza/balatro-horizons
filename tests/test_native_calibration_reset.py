from lupa.lua51 import LuaRuntime

from balatro_horizons.config import ROOT


def test_calibration_reset_restores_nested_profile_and_id_baseline_without_touching_run():
    lua = LuaRuntime()
    lua.execute("""
      calibration = '1'
      os.getenv = function(name) assert(name=='BH_CALIBRATION'); return calibration end
      G = {STATE=1,STATES={MENU=1},SETTINGS={profile=1},sort_id=12,
        PROFILES={{career_stats={wins=0},MEMORY={deck='Red Deck'}}},
        GAME={seed='current-run',money=100}}
    """)
    reset = lua.execute((ROOT / "native/patches/horizons_reset.lua").read_text())
    lua.globals().reset = reset
    lua.execute("""
      reset.before_start()
      G.PROFILES[1].career_stats.wins = 8
      G.PROFILES[1].MEMORY.deck = 'Blue Deck'
      G.sort_id = 999; G.playing_card = 99
      reset.before_start()
      assert(G.PROFILES[1].career_stats.wins==0 and G.PROFILES[1].MEMORY.deck=='Red Deck')
      assert(G.sort_id==12 and G.playing_card==nil)
      assert(G.GAME.money==100 and G.GAME.seed=='current-run') -- native start owns run reset
      G.PROFILES[1].career_stats.wins=3
      reset.before_start()
      assert(G.PROFILES[1].career_stats.wins==0) -- baseline is not aliased
      calibration = '0'
      G.PROFILES[1].career_stats.wins=4; G.sort_id=100
      reset.before_start()
      assert(G.PROFILES[1].career_stats.wins==4 and G.sort_id==100)
      calibration = '1'; G.STATE=2
      assert(not pcall(reset.before_start))
    """)
