--------------------------- MODULE Scheduler ---------------------------
EXTENDS FiniteSets, Integers, Sequences

CONSTANTS Jobs, Workers, NoWorker, MaxAttempts, LeaseLength, MaxTime

ASSUME /\ Jobs # {}
       /\ Workers # {}
       /\ NoWorker \notin Workers
       /\ MaxAttempts \in Nat \ {0}
       /\ LeaseLength \in Nat \ {0}
       /\ MaxTime \in Nat

Statuses == {"unsubmitted", "pending", "leased", "completed", "failed"}
TerminalStatuses == {"completed", "failed"}

LeaseRecords ==
    [job : Jobs,
     worker : Workers,
     token : 1..MaxAttempts,
     expiry : 0..(MaxTime + LeaseLength)]

CommitRecords ==
    [job : Jobs,
     worker : Workers,
     token : 1..MaxAttempts,
     acceptedAt : 0..MaxTime,
     expiry : 0..(MaxTime + LeaseLength)]

VARIABLES status,
          attempts,
          tokens,
          leaseOwner,
          leaseToken,
          leaseExpiry,
          now,
          crashed,
          leaseHistory,
          commits,
          terminalJobs

vars == <<status, attempts, tokens, leaseOwner, leaseToken, leaseExpiry,
          now, crashed, leaseHistory, commits, terminalJobs>>

TypeOK ==
    /\ status \in [Jobs -> Statuses]
    /\ attempts \in [Jobs -> 0..(MaxAttempts + 1)]
    /\ tokens \in [Jobs -> 0..(MaxAttempts + 1)]
    /\ leaseOwner \in [Jobs -> Workers \cup {NoWorker}]
    /\ leaseToken \in [Jobs -> 0..MaxAttempts]
    /\ leaseExpiry \in [Jobs -> 0..(MaxTime + LeaseLength)]
    /\ now \in 0..MaxTime
    /\ crashed \subseteq Workers
    /\ leaseHistory \subseteq LeaseRecords
    /\ commits \in Seq(CommitRecords)
    /\ terminalJobs \subseteq Jobs

Init ==
    /\ status = [j \in Jobs |-> "unsubmitted"]
    /\ attempts = [j \in Jobs |-> 0]
    /\ tokens = [j \in Jobs |-> 0]
    /\ leaseOwner = [j \in Jobs |-> NoWorker]
    /\ leaseToken = [j \in Jobs |-> 0]
    /\ leaseExpiry = [j \in Jobs |-> 0]
    /\ now = 0
    /\ crashed = {}
    /\ leaseHistory = {}
    /\ commits = <<>>
    /\ terminalJobs = {}

Submit(j) ==
    /\ j \in Jobs
    /\ status[j] = "unsubmitted"
    /\ status' = [status EXCEPT ![j] = "pending"]
    /\ UNCHANGED <<attempts, tokens, leaseOwner, leaseToken, leaseExpiry,
                    now, crashed, leaseHistory, commits, terminalJobs>>

Acquire(w, j) ==
    /\ w \in Workers \ crashed
    /\ j \in Jobs
    /\ status[j] = "pending"
    /\ attempts[j] < MaxAttempts
    /\ status' = [status EXCEPT ![j] = "leased"]
    /\ attempts' = [attempts EXCEPT ![j] = @ + 1]
    /\ tokens' = [tokens EXCEPT ![j] = @ + 1]
    /\ leaseOwner' = [leaseOwner EXCEPT ![j] = w]
    /\ leaseToken' = [leaseToken EXCEPT ![j] = tokens[j] + 1]
    /\ leaseExpiry' = [leaseExpiry EXCEPT ![j] = now + LeaseLength]
    /\ leaseHistory' = leaseHistory \cup
           {[job |-> j,
             worker |-> w,
             token |-> tokens[j] + 1,
             expiry |-> now + LeaseLength]}
    /\ UNCHANGED <<now, crashed, commits, terminalJobs>>

Complete(w, j, token) ==
    /\ w \in Workers \ crashed
    /\ j \in Jobs
    /\ status[j] = "leased"
    /\ leaseOwner[j] = w
    /\ leaseToken[j] = token
    /\ now < leaseExpiry[j]
    /\ status' = [status EXCEPT ![j] = "completed"]
    /\ leaseOwner' = [leaseOwner EXCEPT ![j] = NoWorker]
    /\ leaseToken' = [leaseToken EXCEPT ![j] = 0]
    /\ leaseExpiry' = [leaseExpiry EXCEPT ![j] = 0]
    /\ commits' = Append(
           commits,
           [job |-> j,
            worker |-> w,
            token |-> token,
            acceptedAt |-> now,
            expiry |-> leaseExpiry[j]])
    /\ terminalJobs' = terminalJobs \cup {j}
    /\ UNCHANGED <<attempts, tokens, now, crashed, leaseHistory>>

Tick ==
    /\ now < MaxTime
    /\ now' = now + 1
    /\ UNCHANGED <<status, attempts, tokens, leaseOwner, leaseToken,
                    leaseExpiry, crashed, leaseHistory, commits, terminalJobs>>

Expire(j) ==
    /\ j \in Jobs
    /\ status[j] = "leased"
    /\ now >= leaseExpiry[j]
    /\ status' = [status EXCEPT
           ![j] = IF attempts[j] < MaxAttempts THEN "pending" ELSE "failed"]
    /\ leaseOwner' = [leaseOwner EXCEPT ![j] = NoWorker]
    /\ leaseToken' = [leaseToken EXCEPT ![j] = 0]
    /\ leaseExpiry' = [leaseExpiry EXCEPT ![j] = 0]
    /\ terminalJobs' =
           IF attempts[j] = MaxAttempts THEN terminalJobs \cup {j}
           ELSE terminalJobs
    /\ UNCHANGED <<attempts, tokens, now, crashed, leaseHistory, commits>>

Crash(w) ==
    /\ w \in Workers \ crashed
    /\ crashed' = crashed \cup {w}
    /\ UNCHANGED <<status, attempts, tokens, leaseOwner, leaseToken,
                    leaseExpiry, now, leaseHistory, commits, terminalJobs>>

Next ==
    \/ \E j \in Jobs : Submit(j)
    \/ \E w \in Workers, j \in Jobs : Acquire(w, j)
    \/ \E w \in Workers, j \in Jobs, token \in 1..MaxAttempts :
           Complete(w, j, token)
    \/ Tick
    \/ \E j \in Jobs : Expire(j)
    \/ \E w \in Workers : Crash(w)

Spec == Init /\ [][Next]_vars

AcquireAny == \E w \in Workers, j \in Jobs : Acquire(w, j)
CompleteAny ==
    \E w \in Workers, j \in Jobs, token \in 1..MaxAttempts :
        Complete(w, j, token)
ExpireAny == \E j \in Jobs : Expire(j)

ProgressNext ==
    \/ \E j \in Jobs : Submit(j)
    \/ AcquireAny
    \/ CompleteAny
    \/ Tick
    \/ ExpireAny

ProgressSpec ==
    /\ Init
    /\ [][ProgressNext]_vars
    /\ WF_vars(AcquireAny)
    /\ WF_vars(CompleteAny)
    /\ WF_vars(Tick)
    /\ WF_vars(ExpireAny)

L1_EventualTerminal ==
    \A j \in Jobs :
        (status[j] # "unsubmitted") ~> (status[j] \in TerminalStatuses)

S1_UniqueAcceptedCommit ==
    \A j \in Jobs :
        Cardinality({i \in 1..Len(commits) : commits[i].job = j}) <= 1

S2_FencedCompletion ==
    \A i \in 1..Len(commits) :
        LET c == commits[i]
        IN /\ c.token = tokens[c.job]
           /\ c.acceptedAt < c.expiry
           /\ \E h \in leaseHistory :
                 /\ h.job = c.job
                 /\ h.worker = c.worker
                 /\ h.token = c.token
                 /\ h.expiry = c.expiry

S3_TerminalMonotonicity ==
    terminalJobs = {j \in Jobs : status[j] \in TerminalStatuses}

S4_LeaseUniqueness ==
    \A j \in Jobs :
        IF status[j] = "leased"
        THEN /\ leaseOwner[j] \in Workers
             /\ leaseToken[j] = tokens[j]
             /\ leaseToken[j] > 0
        ELSE /\ leaseOwner[j] = NoWorker
             /\ leaseToken[j] = 0
             /\ leaseExpiry[j] = 0

S5_RetryBound ==
    /\ \A j \in Jobs : attempts[j] <= MaxAttempts
    /\ \A j \in Jobs : status[j] = "failed" => attempts[j] = MaxAttempts

=============================================================================
