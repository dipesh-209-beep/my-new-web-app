# Orphan-pair audit & cleanup report - Kathmandu Bus Route Finder

_Generated 2026-09-08T11:06:33Z by clean_data.py_

| Table | Rows before | Rows after |
|---|---|---|
| operators.csv | 29 | 29 |
| stops.csv | 412 | 396 |
| routes.csv | 119 | 113 |
| route_stops.csv | 2165 | 1901 |
| route_operators.csv | 112 | 106 |

## 1. route_stops orphan pairs
- Removed rows: 0

## 2. route_stops re-sequencing
- Routes re-sequenced (1..N, order preserved): 119

## 2b. Stop deduplication (~250m radius candidates)
- Stops actually merged (human-confirmed via stop_dedup_overrides.yaml): 16
    - kept S0069, dropped ['S0074']
    - kept S0069, dropped ['S0091']
    - kept S0069, dropped ['S0114']
    - kept S0055, dropped ['S0340']
    - kept S0005, dropped ['S0206']
    - kept S0045, dropped ['S0012']
    - kept S0110, dropped ['S0021']
    - kept S0269, dropped ['S0037']
    - kept S0044, dropped ['S0336']
    - kept S0056, dropped ['S0277']
    - kept S0063, dropped ['S0235']
    - kept S0345, dropped ['S0064']
    - kept S0202, dropped ['S0080']
    - kept S0103, dropped ['S0175']
    - kept S0229, dropped ['S0122']
    - kept S0159, dropped ['S0215']
- Candidate clusters PENDING human review (not merged): 75
    - ['S0003', 'S0342']
    - ['S0004', 'S0326']
    - ['S0005', 'S0042']
    - ['S0007', 'S_422']
    - ['S0011', 'S_420']
    - ['S0013', 'S0171']
    - ['S0015', 'S0379']
    - ['S0020', 'S0257', 'S_421']
    - ['S0022', 'S0312']
    - ['S0024', 'S0220']
    - ['S0027', 'S0139']
    - ['S0028', 'S0099']
    - ['S0029', 'S_405']
    - ['S0034', 'S0062', 'S_406']
    - ['S0046', 'S0222']
    - ['S0050', 'S0165']
    - ['S0051', 'S0351']
    - ['S0052', 'S0350']
    - ['S0054', 'S0291']
    - ['S0058', 'S0116']
    - ['S0059', 'S0205']
    - ['S0066', 'S0102']
    - ['S0067', 'S0076']
    - ['S0086', 'S0198']
    - ['S0087', 'S0207', 'S0241', 'S0193', 'S_391']
    - ['S0088', 'S0278', 'S_394']
    - ['S0089', 'S_408']
    - ['S0094', 'S0341']
    - ['S0095', 'S_398']
    - ['S0096', 'S0176']
    - ['S0097', 'S0132', 'S_411']
    - ['S0100', 'S0236']
    - ['S0105', 'S0197']
    - ['S0106', 'S0258']
    - ['S0107', 'S_404']
    - ['S0109', 'S0354']
    - ['S0112', 'S0194']
    - ['S0113', 'S0177']
    - ['S0115', 'S0180']
    - ['S0129', 'S0242']
    - ['S0134', 'S0355']
    - ['S0142', 'S0234', 'S_425']
    - ['S0147', 'S_412']
    - ['S0152', 'S0166']
    - ['S0154', 'S0227']
    - ['S0155', 'S0219', 'S0368']
    - ['S0158', 'S0238']
    - ['S0169', 'S0413', 'S0414']
    - ['S0204', 'S0348']
    - ['S0211', 'S0406']
    - ['S0214', 'S_410', 'S_423']
    - ['S0221', 'S0047']
    - ['S0230', 'S0233', 'S_418']
    - ['S0237', 'S0284']
    - ['S0256', 'S0262']
    - ['S0261', 'S0060']
    - ['S0263', 'S0296']
    - ['S0274', 'S0363']
    - ['S0081', 'S0161']
    - ['S0150', 'S0322']
    - ['S0283', 'S0328', 'S_426']
    - ['S0048', 'S0391']
    - ['S0079', 'S0356']
    - ['S0338', 'S0339']
    - ['S0365', 'S0366']
    - ['S0374', 'S0375']
    - ['S0376', 'S0377']
    - ['S0378', 'S0384']
    - ['S0393', 'S0394']
    - ['S0399', 'S0403']
    - ['S0404', 'S0405']
    - ['S0411', 'S0412']
    - ['S0415', 'S0416']
    - ['S0417', 'S0418']
    - ['S0435', 'S0437']

## 2c. Route deduplication (same operator + similar stop set)
- Routes actually merged (human-confirmed via route_dedup_overrides.yaml): 6
    - kept R3068536, dropped R3068548
    - kept R2295734, dropped R2323381
    - kept R2988893, dropped R2989027
    - kept R2988983, dropped R2989052
    - kept R2301205, dropped R2301206
    - kept R3102605, dropped R2301263
- Marked is_bidirectional as a result of merge: ['R3068536', 'R2295734', 'R2988893', 'R2988983', 'R2301205']
- Candidate pairs PENDING human review (not merged): 7
    - R2909799 ("Kamalbinayak-Ratnapark") <-> R2988890 ("Bagbazaar-Kamalbinayak") - stop-set similarity 0.85
    - R2909799 ("Kamalbinayak-Ratnapark") <-> R2988893 ("Bagbazar-Changu") - stop-set similarity 0.77
    - R2988890 ("Bagbazaar-Kamalbinayak") <-> R2988893 ("Bagbazar-Changu") - stop-set similarity 0.91
    - R2282031 ("Ratna Park - Kirtipur") <-> R3020231 ("Ratna Park - Panga Dobato- Nagaun- Bhatkepati") - stop-set similarity 0.74
    - R2295902 ("KattyaniChowk-Sundhara") <-> R2295903 ("Sundhara-Milanchowk-KattyaniChowk") - stop-set similarity 0.75
    - R2295902 ("KattyaniChowk-Sundhara") <-> R2295941 ("Old Baneshwar-Sundhara") - stop-set similarity 0.72
    - R2989012 ("Ratna Park - Daksinkali") <-> R-GAP-05 ("Ratna Park-Pharping") - stop-set similarity 0.72

## 2d. Revisited-stop resolution (return-leg / splice candidates)
- Candidate revisit pairs found: 101 across 41 route(s)
- Rows dropped (human-confirmed verdict: drop_repeats): 119
- Routes collapsed to first occurrence: ['R2276770', 'R2277212', 'R2282101', 'R2294152', 'R2295974', 'R2295986', 'R2301161', 'R2301306', 'R2301357', 'R2301358', 'R2302674', 'R2909799', 'R2975649', 'R2988806', 'R2988809', 'R2988890', 'R2988891', 'R2988893', 'R2988983', 'R2988993', 'R2989027', 'R2989036', 'R2989052', 'R2989074', 'R2989075', 'R3014451', 'R3020174', 'R3020244', 'R3070257', 'R3070262', 'R3070344', 'R3071562', 'R3072924', 'R3074202', 'R3102319']
- Routes confirmed as genuine loop/return-leg: ['R-NY-03']
- Routes PENDING human review: 5

## 3. Route start/end/total stop recomputation
- start_stop_id corrected: 8 -> ['R2294107', 'R2988835', 'R3102605', 'R3014451', 'R3203278', 'R3232098', 'R3297080', 'R3204165']
- end_stop_id corrected: 21 -> ['R2276770', 'R2282101', 'R2294107', 'R2295986', 'R2909799', 'R3020174', 'R3070257', 'R3070262', 'R3070344', 'R3071562', 'R3102319', 'R2295974', 'R2302674', 'R2988806', 'R3014451', 'R3283810', 'R3255302', 'R-NY-04', 'R-NY-05', 'R-NY-06', 'R-NY-07']
- total_stops corrected: 33 -> ['R2276770', 'R2277212', 'R2282101', 'R2295986', 'R2301306', 'R2301357', 'R2301358', 'R2909799', 'R2975649', 'R2988809', 'R2988890', 'R2988891', 'R2988893', 'R2988983', 'R2988993', 'R2989036', 'R2989074', 'R2989075', 'R3020174', 'R3020244', 'R3070257', 'R3070262', 'R3070344', 'R3071562', 'R3074202', 'R3102319', 'R2295974', 'R2301161', 'R3072924', 'R2294152', 'R2302674', 'R2988806', 'R3014451']

## 4. routes.operator_id orphan references
- Invalid operator_id values: []
- Routes nulled (unrecoverable): 0 -> []

## 5. Distance outlier flags
- Routes flagged distance_flagged_for_recompute: 32 -> ['R2276770', 'R2294107', 'R2295902', 'R2295903', 'R2295942', 'R2295986', 'R2301306', 'R2909799', 'R2975649', 'R2988890', 'R2988891', 'R2988893', 'R2988983', 'R2988993', 'R2989036', 'R3020174', 'R3020244', 'R3068536', 'R3070257', 'R3070262', 'R3071562', 'R3102605', 'R2301161', 'R3072924', 'R2302674', 'R2988806', 'R3014451', 'R3214592', 'R3204165', 'R-GAP-14', 'R-GAP-15', 'R-GAP-17']

## 6. Post-cleanup verification (must all read 0)
- route_stops.stop_id not in stops: 0
- route_stops.route_id not in routes: 0
- route_operators.route_id not in routes: 0
- route_operators.operator_id not in operators: 0
- routes.operator_id not in operators (excl. NULL): 0
- routes.start_stop_id not in stops: 0
- routes.end_stop_id not in stops: 0
- routes.total_stops mismatched vs actual route_stops count: 0

## 7. Revisited stops remaining after resolution
- 18 route_stops rows still revisit a stop_id across 6 route(s).