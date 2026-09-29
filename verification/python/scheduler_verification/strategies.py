from hypothesis import strategies as st


JOB_IDS = st.sampled_from(["job-a", "job-b"])
WORKER_IDS = st.sampled_from(["worker-1", "worker-2"])
TOKENS = st.integers(min_value=1, max_value=4)
DELTAS = st.integers(min_value=1, max_value=2)
