"""Reviewed whole-module AST upgrades; applied to historical source only.

Every mapping requires an exact old module AST, never an arbitrary method body.
Additions additionally require an exact historical full-source fingerprint.
The current side is never normalized: reverting a fix cannot inherit approval.
"""

# Exact source versions reviewed in 5ad4219, 2d31ce0 and 75d384e.
# See docs/restore-compatibility.md for the scope of each named upgrade.
MODULE_UPGRADES = {
    # #57: explicit calibration and production release gating on resumed work.
    "service_execution.py": ((
        "e7cc23fb5b1ad7f5336fff0d50f4978df33e6dc339b5f22f853889a2d0c8d0d7",
    ), "d63d133f8384e35fe42be656e898a4b0ab99e94203599484f1ab79d47fc420bc"),
    "service.py": ((
        "e8bb378f81a13d10444b4e21cf7987ab3b65ad6bbfa5277d28f1bb55247d61c5",
    ), "a8b25b681d849fcbb20145e97d3bf6d46e8b208055c9bbf3658bc416f65672e6"),
    # #57: one checked recovery replaces diagnostic-certificate admission.
    "workbench/budget_continuation.py": ((
        "8c3f45962ff97bde525471ac2ae493277c7018edb14489afa06d45ff6ba4fb73",
    ), "22f30ca6762a1479739e1ba0a9c9ebcc877bfea6854fcc616d88b734102f0956"),
    "workbench/branches.py": ((
        "1166831119543d43222793169a3de37636a1761f79c8ee138012a228a634f80e",
    ), "6a02a64d294122d63bec73b8814d6f0c466e01cda65f72a9a7461708fb0bf958"),
    "evidence/certification.py": ((
        "5c8e2d30ce291cc8b96f5d71f235ae292794167680214c6d22a8c02e90ffcc79",
    ), "9f588aac5471cf17a06ec0ca933963da933b81e2ba758f6c25c948fa81237846"),
    "evidence/release.py": ((
        "885abdce352ec557f6debb115e0fffbf5bd6b8ae181f3a39feae84fc2c1a49d1",
    ), "9c5e0336699ddd1671cc49c72d6b318153c537958d47e7cf64d278a83d485ea7"),
    # #59: frozen protocol stays unchanged; bind executor and persist the proof.
    "harness/decision.py": ((
        "edaa542c633767dcdfe522776df8ddfc2f27d094099bf6b5bbe20af3d7c8936c",
    ), "c5044c989623e1a1e4d5c1248cf374029cfd4c147041aae613fa3453944df984"),
    "harness/context/freeze.py": ((
        "0446046abd98c58b39b0eb702da58f2f520ee7cc2389880e2cb86cdcec3d344b",
    ), "35a119f42a3ba274532b15d39b71cb5cd6f05d4cd5fbd84220b70bde8071cbb3"),
    "harness/runtime.py": ((
        "e10fe9e3d9cf67cd3385877a5b19f93c2e22979010d97ed41f54d1c4ec3b8dfa",
        "58aac748a405a53c7ce2181d5f2595dd450b8fe6e3a9e4dee254db1e32f97717",
    ), "ec01601985aa28c5bb8c87ed7542c6a6bbf40dc4a4afea20c1cac48bcbd67094"),
    "evidence/provenance.py": ((
        "cd3dec6cf4a6f90969a42ca33faca4ad33f275c6109d95c58f08a5d98eb43757",
    ), "924baa856c71e7b098a39ed290f28ee069d64e8b306f6a63b57f7dc081dfeadc"),
    "evidence/recovery.py": ((
        "a2a690ac2fd745a56c50d1f64d0cdc5417717f2ff92564a5a7370ab827bfc1df",
        "099de919cec3a97018ebfb8997b995192127f9464de4adb6cc18b903f0c7c95d",
    ), "a4ee1464702ffa779412d07dba044070649134f9021d2311777e2cd6e966aa09"),
    # #59 repair: terminal outcomes, shared row validation and public provenance.
    "harness/terminals.py": ((
        "0311c2b3f8df3554133c3fe6caec1a53a9c9d78926b09005b943a0277a155bb8",
    ), "8acf91b8982bedc06b5094c7b8c856423509a4938b950a57a3e8e0f37a7a763c"),
    "workbench/budget_ledger.py": ((
        "c96e4813404206750fb92d19ba390bf88204399ab6b495071b1285e155cfcc14",
        "099ecd8225d52bae0dcad1e56eae899fdd9e91cb934094b2cf9d509994dcf5f8",
    ), "35f05394ab9cf0b57303e5b3f097f532d0def582c9456d0368cb78d87d47c98e"),
    "workbench/restore_ledger.py": ((
        "52d6516dd1a2574a3a72f358603bc964fde17c81d2ff85bdf3bc0ff8807d425b",
    ), "1c268fbcb4bcbef87702d622538418bf4b2b8e6c719cb7fdaa84ce373d71f1dd"),
    "workbench/restoration.py": ((
        "1510bb023935f1a58c452c5bcd163fea117c3c0927a9364abf919bb4f8139fd5",
    ), "4c8ed24f20172df90c8f2f0c15d700dddf660e6c80f33c1601d954da7bb2555c"),
}

RESTORE_ADDITIONS = {
    "service_restore.py": "2eae24558ea764c6dc7d71538514c149a67875ff09fb7195b1e981f949aa0038",
    "workbench/restore_ledger.py": MODULE_UPGRADES["workbench/restore_ledger.py"][1],
    "workbench/restoration.py": MODULE_UPGRADES["workbench/restoration.py"][1],
}
SOURCE_ADDITIONS = {
    # 5ad42191e760388e4ebb953bb13c2ebb84a48dae, before #57 and #59.
    "f2e1f4822f4ebf202b7b70ef1a21d64da82e38f2943a90097bf5074c50e40276": {
        **RESTORE_ADDITIONS, "evidence/recovery.py": MODULE_UPGRADES["evidence/recovery.py"][1],
    },
    # 2d31ce02b94df8f465b13724226acdc9bb11ee60, before #59.
    "5728d051aa0f7eb7498be3c1c0d880c77e718e0a7c8274ae2ee50e3a436fb87c": RESTORE_ADDITIONS,
}

# Funding controls upgrade only the exact deployed 16x release, 8807caf.
# Keep the earlier 1x catalogue intact; it does not authorize runtime migration.
FUNDING_SOURCE = "2f56a4abf969435330ba6decf8569a7749cdb463327ee40343b3634e359324aa"
FUNDING_UPGRADES = {
    "config.py": (
        "079b970a0b8634c058f04736626c5e51a062d02084f90fc36ca369dd034d4f6e",
        "bdc41fd6d5137ce7c0ecfe5c954a98d69ae6bea74fe66d88f827e6f95799f2bd"),
    "cost_limits.py": (None, "04b7379bfd8adece1f41b1020ace043b5ebd23c4d71cd1691122b3ad911a10b2"),
    "evidence/provenance.py": (
        "924baa856c71e7b098a39ed290f28ee069d64e8b306f6a63b57f7dc081dfeadc",
        "36cb2ee8347bd6cfa0655b3246d543089fe58b0e88c77a82e880d8bf74997025"),
    "harness/context/freeze.py": (
        "35a119f42a3ba274532b15d39b71cb5cd6f05d4cd5fbd84220b70bde8071cbb3",
        "05b657ce9626e7b9f8a752c955023b4d15e5be761c75f45d35c488883c818e02"),
    "harness/money.py": (
        "32432ea9c53b87a47e98144bfcc5d581ef8eedd21e43400294b049f001a05416",
        "2861cb6ce6aa13b320ed54ad13e715227f15b45a71e3c47c939c6b39fd2967c0"),
    "service.py": (
        "a8b25b681d849fcbb20145e97d3bf6d46e8b208055c9bbf3658bc416f65672e6",
        "8706bbf0bfb55c645354cb3e32ec98d5cf787c4e29ce654c11b310bea9d1cbc3"),
    "service_restore.py": (
        "2eae24558ea764c6dc7d71538514c149a67875ff09fb7195b1e981f949aa0038",
        "47cbf889b942ab201f2c670fe8a815357bd709b240be3ad4508ebe7cb5c29583"),
    "workbench/budget_continuation.py": (
        "22f30ca6762a1479739e1ba0a9c9ebcc877bfea6854fcc616d88b734102f0956",
        "5a86ae2dc42b78a358d707f555fe8d010ab1ecf811fb1faca97da4c26c76d02d"),
    "workbench/restoration.py": (
        "4c8ed24f20172df90c8f2f0c15d700dddf660e6c80f33c1601d954da7bb2555c",
        "03f55cca6d58e688f522f7742533777ac1fef28dd523587aaad7b306a843aa60"),
}


def upgrade_historical_manifest(manifest, source_hash):
    result = dict(manifest)
    prefix = "src/balatro_horizons/"
    for path, (previous, accepted) in MODULE_UPGRADES.items():
        name = prefix + path
        if result.get(name) in previous:
            result[name] = accepted
    for path, accepted in SOURCE_ADDITIONS.get(source_hash, {}).items():
        result.setdefault(prefix + path, accepted)
    if source_hash == FUNDING_SOURCE:
        for path, (previous, accepted) in FUNDING_UPGRADES.items():
            name = prefix + path
            if result.get(name) == previous:
                result[name] = accepted
    return result
