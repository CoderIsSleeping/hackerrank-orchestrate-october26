Baseline snapshot before the recall investigation.
commit: 9e51db892365f80b2d65ef5f97429baafd24e9d0
offline metrics (32 samples): status 32/32, step P 100%, R 86.0%, F1 92.5%, exact sequence 15/32; 17/17 contract checks on output.csv.
Reproduce: git checkout 9e51db8 && LLM_PROVIDER=none python code/main.py
