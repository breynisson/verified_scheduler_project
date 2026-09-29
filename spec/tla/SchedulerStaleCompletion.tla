-------------------- MODULE SchedulerStaleCompletion --------------------
EXTENDS Scheduler

StaleComplete(w, j, token) ==
    /\ w \in Workers
    /\ j \in Jobs
    /\ status[j] = "leased"
    /\ w # leaseOwner[j]
    /\ token # tokens[j]
    /\ \E h \in leaseHistory :
          /\ h.job = j
          /\ h.worker = w
          /\ h.token = token
    /\ LET oldLease == CHOOSE h \in leaseHistory :
                           /\ h.job = j
                           /\ h.worker = w
                           /\ h.token = token
       IN /\ status' = [status EXCEPT ![j] = "completed"]
          /\ leaseOwner' = [leaseOwner EXCEPT ![j] = NoWorker]
          /\ leaseToken' = [leaseToken EXCEPT ![j] = 0]
          /\ leaseExpiry' = [leaseExpiry EXCEPT ![j] = 0]
          /\ commits' = Append(
                 commits,
                 [job |-> j,
                  worker |-> w,
                  token |-> token,
                  acceptedAt |-> now,
                  expiry |-> oldLease.expiry])
          /\ terminalJobs' = terminalJobs \cup {j}
    /\ UNCHANGED <<attempts, tokens, now, crashed, leaseHistory>>

BuggyNext ==
    Next \/ \E w \in Workers, j \in Jobs, token \in 1..MaxAttempts :
                StaleComplete(w, j, token)

BuggySpec == Init /\ [][BuggyNext]_vars

=============================================================================
