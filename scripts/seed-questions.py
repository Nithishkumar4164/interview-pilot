#!/usr/bin/env python3
"""Seed the question bank with 50+ interview questions.

Usage:
    python scripts/seed-questions.py

Requires DATABASE_URL to be set (or uses the default from config).
"""

import asyncio
import sys
from pathlib import Path

# Add backend src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "backend"))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.config import settings
from src.models.base import Base
from src.models.plan import QuestionBank


SEED_QUESTIONS = [
    # ═══ CODING — Easy (difficulty 1) ═══════════════════════════════════════
    {
        "text": "Given an array of integers, return indices of the two numbers that add up to a specific target.",
        "difficulty": 1,
        "category": "coding",
        "tags": ["arrays", "hash_map", "fundamentals"],
        "expected_time_min": 15,
        "optimal_approaches": [
            {
                "name": "Hash Map",
                "data_structures": ["hash_map"],
                "expected_complexity": {"time": "O(n)", "space": "O(n)"},
                "key_steps": ["Iterate through array", "For each element, check if complement exists in map", "Store element-to-index mapping"],
            },
            {
                "name": "Two Pointers (sorted)",
                "data_structures": ["array"],
                "expected_complexity": {"time": "O(n log n)", "space": "O(1)"},
                "key_steps": ["Sort the array (with original indices)", "Use two pointers from both ends", "Move pointers inward based on sum"],
            },
        ],
        "common_pitfalls": ["Using the same element twice", "Returning values instead of indices", "Not handling duplicates"],
        "hints": [
            {"level": 1, "text": "What data structure gives O(1) lookups?"},
            {"level": 2, "text": "A hash map could store each number's index. What would you look up?"},
            {"level": 3, "text": "For each number, check if (target - number) is already in your hash map."},
        ],
        "alternates": [
            "Find all pairs of numbers that sum to a target.",
            "Find three numbers that add up to a target (3Sum).",
        ],
    },
    {
        "text": "Reverse a singly linked list iteratively and recursively.",
        "difficulty": 1,
        "category": "coding",
        "tags": ["linked_list", "fundamentals", "recursion"],
        "expected_time_min": 15,
        "optimal_approaches": [
            {
                "name": "Iterative",
                "data_structures": ["linked_list"],
                "expected_complexity": {"time": "O(n)", "space": "O(1)"},
                "key_steps": ["Track prev, curr, next pointers", "Reverse curr.next to prev", "Advance all pointers"],
            },
        ],
        "common_pitfalls": ["Losing reference to next node", "Not returning the new head", "Stack overflow on recursive with large lists"],
        "hints": [
            {"level": 1, "text": "You need three pointers. What state do you need to track?"},
            {"level": 2, "text": "Track previous, current, and next. What happens at each step?"},
            {"level": 3, "text": "Save next = curr.next, set curr.next = prev, then advance prev = curr, curr = next."},
        ],
        "alternates": ["Reverse a linked list in groups of K."],
    },
    {
        "text": "Check if a string has all unique characters. What if you cannot use additional data structures?",
        "difficulty": 1,
        "category": "coding",
        "tags": ["strings", "fundamentals", "bit_manipulation"],
        "expected_time_min": 10,
        "optimal_approaches": [
            {
                "name": "Hash Set",
                "data_structures": ["hash_set"],
                "expected_complexity": {"time": "O(n)", "space": "O(n)"},
                "key_steps": ["Add each character to a set", "If already present, return false"],
            },
            {
                "name": "Bit Vector",
                "data_structures": ["integer"],
                "expected_complexity": {"time": "O(n)", "space": "O(1)"},
                "key_steps": ["Use bits of an integer as flags for each character", "Check and set bit for each char"],
            },
        ],
        "common_pitfalls": ["Not considering character encoding (ASCII vs Unicode)", "Not asking about the character set"],
        "hints": [
            {"level": 1, "text": "What is the simplest way to track which characters you've seen?"},
            {"level": 2, "text": "A set would work. For the follow-up, think about using bits."},
            {"level": 3, "text": "Use an integer as a bit vector: check bit at (char - 'a'), set it if not present."},
        ],
        "alternates": ["Determine if two strings are permutations of each other."],
    },
    {
        "text": "Implement a function to check if a binary tree is balanced (heights of subtrees differ by at most 1).",
        "difficulty": 1,
        "category": "coding",
        "tags": ["trees", "recursion", "fundamentals"],
        "expected_time_min": 15,
        "optimal_approaches": [
            {
                "name": "Bottom-Up DFS",
                "data_structures": ["binary_tree"],
                "expected_complexity": {"time": "O(n)", "space": "O(h)"},
                "key_steps": ["Recursively compute height", "Return -1 if unbalanced", "Check height difference at each node"],
            },
        ],
        "common_pitfalls": ["O(n^2) solution that recomputes heights", "Not handling null nodes correctly"],
        "hints": [
            {"level": 1, "text": "Can you combine the balance check with the height computation?"},
            {"level": 2, "text": "Return the height from each recursive call, but use a sentinel value for unbalanced."},
            {"level": 3, "text": "Return -1 if any subtree is unbalanced. Otherwise return max(left, right) + 1."},
        ],
        "alternates": ["Validate if a binary tree is a BST."],
    },
    # ═══ CODING — Medium (difficulty 2-3) ════════════════════════════════════
    {
        "text": "Design and implement an LRU cache with O(1) get and put operations.",
        "difficulty": 3,
        "category": "coding",
        "tags": ["data_structures", "design_patterns", "hash_map", "linked_list"],
        "expected_time_min": 25,
        "optimal_approaches": [
            {
                "name": "Hash Map + Doubly Linked List",
                "data_structures": ["hash_map", "doubly_linked_list"],
                "expected_complexity": {"time": "O(1)", "space": "O(capacity)"},
                "key_steps": ["Hash map maps key to DLL node", "Most recently used at head", "Evict from tail on capacity overflow"],
            },
        ],
        "common_pitfalls": ["Not maintaining both map and list in sync", "Edge cases with capacity 1", "Thread safety concerns"],
        "hints": [
            {"level": 1, "text": "You need O(1) access AND O(1) removal/reordering. Which two data structures together can achieve this?"},
            {"level": 2, "text": "Combine a hash map (for O(1) lookup) with a doubly linked list (for O(1) removal)."},
            {"level": 3, "text": "Map keys to DLL nodes. On get: move node to head. On put: add to head, evict tail if full."},
        ],
        "alternates": ["Implement an LFU cache."],
    },
    {
        "text": "Given a matrix of 0s and 1s, find the number of islands (connected groups of 1s).",
        "difficulty": 2,
        "category": "coding",
        "tags": ["graphs", "bfs", "dfs", "data_structures"],
        "expected_time_min": 20,
        "optimal_approaches": [
            {
                "name": "DFS Flood Fill",
                "data_structures": ["matrix", "stack"],
                "expected_complexity": {"time": "O(m*n)", "space": "O(m*n)"},
                "key_steps": ["Iterate all cells", "On finding a 1, increment count and DFS to mark connected 1s as visited"],
            },
            {
                "name": "BFS Flood Fill",
                "data_structures": ["matrix", "queue"],
                "expected_complexity": {"time": "O(m*n)", "space": "O(min(m,n))"},
                "key_steps": ["Same as DFS but use a queue", "Better for very deep recursion scenarios"],
            },
        ],
        "common_pitfalls": ["Not handling diagonal connections (clarify with interviewer)", "Modifying input without asking", "Stack overflow on large grids with DFS"],
        "hints": [
            {"level": 1, "text": "Think of each cell as a graph node. How would you traverse connected components?"},
            {"level": 2, "text": "Use DFS or BFS from each unvisited '1'. Each traversal represents one island."},
            {"level": 3, "text": "For each cell (i,j) == 1 that's unvisited: increment island count, then DFS/BFS marking all connected 1s as visited."},
        ],
        "alternates": ["Find the largest island by area."],
    },
    {
        "text": "Implement a trie (prefix tree) that supports insert, search, and startsWith operations.",
        "difficulty": 2,
        "category": "coding",
        "tags": ["data_structures", "trees", "strings"],
        "expected_time_min": 20,
        "optimal_approaches": [
            {
                "name": "Trie Node with Hash Map Children",
                "data_structures": ["trie", "hash_map"],
                "expected_complexity": {"time": "O(m) per operation", "space": "O(n*m)"},
                "key_steps": ["TrieNode has children dict and is_end flag", "Insert: walk/create nodes", "Search/startsWith: walk existing nodes"],
            },
        ],
        "common_pitfalls": ["Confusing search and startsWith", "Not marking end-of-word nodes"],
        "hints": [
            {"level": 1, "text": "Each node should represent a character. How do you track children?"},
            {"level": 2, "text": "Use a dictionary mapping characters to child nodes. Add an is_end boolean."},
            {"level": 3, "text": "Insert: traverse creating nodes. Search: traverse checking is_end at the end. StartsWith: just traverse."},
        ],
        "alternates": ["Add a method to find all words with a given prefix."],
    },
    {
        "text": "Find the longest substring without repeating characters.",
        "difficulty": 2,
        "category": "coding",
        "tags": ["strings", "sliding_window", "hash_map"],
        "expected_time_min": 20,
        "optimal_approaches": [
            {
                "name": "Sliding Window",
                "data_structures": ["hash_map"],
                "expected_complexity": {"time": "O(n)", "space": "O(min(m,n))"},
                "key_steps": ["Maintain a window with left/right pointers", "Expand right, contract left on duplicate", "Track max length"],
            },
        ],
        "common_pitfalls": ["Off-by-one in window size calculation", "Not updating left pointer correctly on duplicates"],
        "hints": [
            {"level": 1, "text": "Can you use a sliding window approach? What defines the window boundaries?"},
            {"level": 2, "text": "Use two pointers. The right pointer expands. When you hit a duplicate, move the left pointer past the last occurrence."},
            {"level": 3, "text": "Map each character to its latest index. When char repeats, set left = max(left, map[char] + 1). Update max length = right - left + 1."},
        ],
        "alternates": ["Find the longest substring with at most K distinct characters."],
    },
    {
        "text": "Serialize and deserialize a binary tree.",
        "difficulty": 3,
        "category": "coding",
        "tags": ["trees", "data_structures", "design_patterns"],
        "expected_time_min": 25,
        "optimal_approaches": [
            {
                "name": "Preorder with Null Markers",
                "data_structures": ["binary_tree", "queue"],
                "expected_complexity": {"time": "O(n)", "space": "O(n)"},
                "key_steps": ["Serialize: preorder traversal with null markers", "Deserialize: consume tokens recursively"],
            },
            {
                "name": "BFS Level Order",
                "data_structures": ["binary_tree", "queue"],
                "expected_complexity": {"time": "O(n)", "space": "O(n)"},
                "key_steps": ["Serialize: BFS with null markers", "Deserialize: BFS reconstruction"],
            },
        ],
        "common_pitfalls": ["Not handling null nodes in serialization", "Delimiter/escaping issues", "Not restoring tree structure correctly"],
        "hints": [
            {"level": 1, "text": "What traversal order would let you reconstruct the tree unambiguously?"},
            {"level": 2, "text": "Preorder with explicit null markers. Use a delimiter between values."},
            {"level": 3, "text": "Serialize: preorder, write 'null' for None. Deserialize: pop from queue, recursively build left then right."},
        ],
        "alternates": ["Serialize and deserialize a BST (can you do it without null markers?)."],
    },
    {
        "text": "Given an array of meeting intervals [start, end], find the minimum number of conference rooms required.",
        "difficulty": 2,
        "category": "coding",
        "tags": ["sorting", "heap", "intervals"],
        "expected_time_min": 20,
        "optimal_approaches": [
            {
                "name": "Min Heap",
                "data_structures": ["heap"],
                "expected_complexity": {"time": "O(n log n)", "space": "O(n)"},
                "key_steps": ["Sort by start time", "Use min-heap of end times", "If earliest end <= current start, pop", "Push current end, track max heap size"],
            },
            {
                "name": "Chronological Ordering",
                "data_structures": ["array"],
                "expected_complexity": {"time": "O(n log n)", "space": "O(n)"},
                "key_steps": ["Separate starts and ends", "Sort both", "Two-pointer scan counting active meetings"],
            },
        ],
        "common_pitfalls": ["Not sorting first", "Off-by-one with overlapping boundaries", "Using max-heap instead of min-heap"],
        "hints": [
            {"level": 1, "text": "If you process meetings in start-time order, what do you need to track?"},
            {"level": 2, "text": "Keep track of when each room becomes free. A min-heap of end times works well."},
            {"level": 3, "text": "Sort by start time. For each meeting, if min-heap top <= start, pop it (room freed). Push end time. Max heap size is the answer."},
        ],
        "alternates": ["Merge overlapping intervals."],
    },
    {
        "text": "Implement a min stack that supports push, pop, top, and getMin in O(1) time.",
        "difficulty": 2,
        "category": "coding",
        "tags": ["stacks", "data_structures", "fundamentals"],
        "expected_time_min": 15,
        "optimal_approaches": [
            {
                "name": "Two Stacks",
                "data_structures": ["stack"],
                "expected_complexity": {"time": "O(1) all ops", "space": "O(n)"},
                "key_steps": ["Main stack for values", "Min stack tracks running minimum", "Push to min stack when value <= current min"],
            },
        ],
        "common_pitfalls": ["Not handling equal values on min stack correctly", "Popping from empty min stack"],
        "hints": [
            {"level": 1, "text": "How can you track the minimum as elements are pushed and popped?"},
            {"level": 2, "text": "Use a second stack that mirrors the minimum value at each level."},
            {"level": 3, "text": "Push to min stack whenever value <= current min. Pop from min stack when main stack top equals min stack top."},
        ],
        "alternates": ["Implement a max stack with popMax support."],
    },
    # ═══ CODING — Hard (difficulty 4-5) ══════════════════════════════════════
    {
        "text": "Design a data structure that supports adding words and searching with wildcards (. matches any character).",
        "difficulty": 4,
        "category": "coding",
        "tags": ["data_structures", "trees", "backtracking", "design_patterns"],
        "expected_time_min": 30,
        "optimal_approaches": [
            {
                "name": "Trie with DFS for Wildcards",
                "data_structures": ["trie"],
                "expected_complexity": {"time": "O(m) add, O(26^m) worst case search", "space": "O(n*m)"},
                "key_steps": ["Build a trie for addWord", "For search, DFS: on '.', try all children", "On exact char, follow that child only"],
            },
        ],
        "common_pitfalls": ["Not handling wildcard at the end", "Not checking is_end for exact matches", "Exponential branching with multiple wildcards"],
        "hints": [
            {"level": 1, "text": "What data structure efficiently handles prefix-based operations?"},
            {"level": 2, "text": "A Trie for insertion. For wildcards, you need to explore multiple branches."},
            {"level": 3, "text": "Add to trie normally. For search with '.', recurse on all children at that position."},
        ],
        "alternates": ["Design an autocomplete system."],
    },
    {
        "text": "Given a stream of integers, find the median at any point. Design for efficiency with addNum and findMedian methods.",
        "difficulty": 3,
        "category": "coding",
        "tags": ["heap", "data_structures", "algorithms"],
        "expected_time_min": 25,
        "optimal_approaches": [
            {
                "name": "Two Heaps",
                "data_structures": ["max_heap", "min_heap"],
                "expected_complexity": {"time": "O(log n) add, O(1) find", "space": "O(n)"},
                "key_steps": ["Max-heap for lower half, min-heap for upper half", "Balance heaps after each insert", "Median from tops"],
            },
        ],
        "common_pitfalls": ["Not keeping heaps balanced", "Wrong median calculation for even/odd counts"],
        "hints": [
            {"level": 1, "text": "If the numbers were sorted, the median is in the middle. How can you efficiently maintain the 'middle'?"},
            {"level": 2, "text": "Split numbers into a lower half and upper half. What data structure lets you peek at the max of lower and min of upper?"},
            {"level": 3, "text": "Use a max-heap for lower half, min-heap for upper. Rebalance so sizes differ by at most 1. Median is from the larger heap's top (or average of both tops)."},
        ],
        "alternates": ["Find the Kth largest element in a stream."],
    },
    {
        "text": "Implement a rate limiter (token bucket algorithm) that is thread-safe and supports multiple rate limits.",
        "difficulty": 4,
        "category": "coding",
        "tags": ["concurrency", "design_patterns", "algorithms"],
        "expected_time_min": 30,
        "optimal_approaches": [
            {
                "name": "Token Bucket",
                "data_structures": ["counter"],
                "expected_complexity": {"time": "O(1) per request", "space": "O(1)"},
                "key_steps": ["Track tokens and last refill time", "On request: refill tokens based on elapsed time", "Consume token if available"],
            },
            {
                "name": "Sliding Window Counter",
                "data_structures": ["counter"],
                "expected_complexity": {"time": "O(1) per request", "space": "O(1)"},
                "key_steps": ["Track request counts per time window", "Weighted count between current and previous window"],
            },
        ],
        "common_pitfalls": ["Race conditions without proper locking", "Floating point errors in time calculations", "Not handling burst traffic"],
        "hints": [
            {"level": 1, "text": "Think about how tokens are replenished over time. What do you need to track?"},
            {"level": 2, "text": "Store the token count and last refill timestamp. On each request, calculate how many tokens should have been added since last refill."},
            {"level": 3, "text": "tokens += (now - last_refill) * rate. Cap at max. If tokens >= 1, allow and decrement. Use a mutex for thread safety."},
        ],
        "alternates": ["Design a distributed rate limiter using Redis."],
    },
    {
        "text": "Implement a concurrent hash map with lock striping for improved parallelism.",
        "difficulty": 5,
        "category": "coding",
        "tags": ["concurrency", "data_structures", "scalability"],
        "expected_time_min": 35,
        "optimal_approaches": [
            {
                "name": "Lock Striping",
                "data_structures": ["hash_map", "array_of_locks"],
                "expected_complexity": {"time": "O(1) average", "space": "O(n + num_stripes)"},
                "key_steps": ["Partition buckets into stripes", "Each stripe has its own lock", "Hash determines bucket and stripe"],
            },
        ],
        "common_pitfalls": ["Deadlocks during resize", "Not handling resize atomically", "Too few or too many stripes"],
        "hints": [
            {"level": 1, "text": "A single global lock is too coarse. How can you allow more parallelism?"},
            {"level": 2, "text": "Instead of one lock, use N locks. Each covers a range of buckets. This is called lock striping."},
            {"level": 3, "text": "Create an array of locks (e.g., 16). stripe_index = hash(key) % num_stripes. Lock only that stripe's lock for operations."},
        ],
        "alternates": ["Implement a lock-free queue using CAS operations."],
    },
    # ═══ SYSTEM DESIGN ══════════════════════════════════════════════════════
    {
        "text": "Design a URL shortening service like bit.ly. Cover the full lifecycle: creation, redirection, analytics, and expiration.",
        "difficulty": 2,
        "category": "system_design",
        "tags": ["web_services", "databases", "scalability", "fundamentals"],
        "expected_time_min": 25,
        "optimal_approaches": [
            {
                "name": "Hash + Counter Based",
                "data_structures": ["hash_map", "counter"],
                "expected_complexity": {"time": "O(1) create/redirect", "space": "O(n URLs)"},
                "key_steps": ["Base62 encode auto-incrementing ID", "Store mapping in DB with TTL", "301/302 redirect", "Async analytics pipeline"],
            },
        ],
        "common_pitfalls": ["Not addressing collision handling", "Not discussing read vs write ratio", "Missing analytics at scale"],
        "hints": [
            {"level": 1, "text": "Start with the API design and core data model. What operations are needed?"},
            {"level": 2, "text": "Consider: how do you generate unique short codes? What's the read:write ratio? How do you handle high redirect throughput?"},
            {"level": 3, "text": "Use base62-encoded auto-increment IDs. Cache popular URLs in Redis. Async log redirects for analytics. DB sharding by short code."},
        ],
        "alternates": ["Design a paste service like Pastebin."],
    },
    {
        "text": "Design a real-time chat system supporting 1-on-1 and group messaging, with delivery receipts and typing indicators.",
        "difficulty": 3,
        "category": "system_design",
        "tags": ["real_time", "distributed_systems", "websockets"],
        "expected_time_min": 30,
        "optimal_approaches": [
            {
                "name": "WebSocket + Message Queue",
                "data_structures": ["message_queue", "connection_registry"],
                "expected_complexity": {"time": "Real-time delivery", "space": "O(messages)"},
                "key_steps": ["WebSocket connections per user", "Publish-subscribe for group messages", "Message persistence with delivery status", "Presence tracking"],
            },
        ],
        "common_pitfalls": ["Not handling offline users", "Missing message ordering guarantees", "Not addressing group scalability"],
        "hints": [
            {"level": 1, "text": "Start with the real-time connection model. How does a client stay connected?"},
            {"level": 2, "text": "WebSockets for persistent connections. How do you route a message to the right server when users are distributed?"},
            {"level": 3, "text": "Connection registry maps user → server. Use pub/sub (Redis/Kafka) for cross-server delivery. Store messages in DB, track delivery status per recipient."},
        ],
        "alternates": ["Design a notification system."],
    },
    {
        "text": "Design a distributed task scheduler that can handle millions of scheduled jobs with at-least-once execution guarantees.",
        "difficulty": 4,
        "category": "system_design",
        "tags": ["distributed_systems", "scalability", "architecture"],
        "expected_time_min": 35,
        "optimal_approaches": [
            {
                "name": "Partitioned Time Wheel + Worker Pool",
                "data_structures": ["priority_queue", "distributed_lock"],
                "expected_complexity": {"time": "O(1) schedule, O(1) fire", "space": "O(jobs)"},
                "key_steps": ["Partition jobs by time bucket", "Leader election per partition", "Dequeue and execute with retry", "Dead letter queue for failures"],
            },
        ],
        "common_pitfalls": ["Not handling worker failures", "Clock skew in distributed environment", "Missing idempotency"],
        "hints": [
            {"level": 1, "text": "How do you efficiently find which jobs are ready to execute at any given time?"},
            {"level": 2, "text": "Consider partitioning by time. Each partition can be processed independently. How do you ensure at-least-once?"},
            {"level": 3, "text": "Time-bucketed partitions in a DB or Redis sorted set. Workers claim partitions via distributed locks. Mark jobs claimed before executing. Retry unclaimed jobs."},
        ],
        "alternates": ["Design a cron-like recurring job system."],
    },
    {
        "text": "Design a content delivery network (CDN). Cover caching strategies, cache invalidation, and geographic routing.",
        "difficulty": 4,
        "category": "system_design",
        "tags": ["distributed_systems", "caching", "scalability", "architecture"],
        "expected_time_min": 35,
        "optimal_approaches": [
            {
                "name": "Hierarchical Cache with DNS Routing",
                "data_structures": ["distributed_cache", "consistent_hashing"],
                "expected_complexity": {"time": "O(1) cache hit", "space": "O(content_size * replication)"},
                "key_steps": ["DNS-based geographic routing", "Edge → Regional → Origin tier", "Consistent hashing for cache distribution", "TTL + push-based invalidation"],
            },
        ],
        "common_pitfalls": ["Not discussing cache invalidation strategies", "Missing hot-spot handling", "Not addressing origin shield"],
        "hints": [
            {"level": 1, "text": "Start with the core goal: serving content from geographically close servers. How does the client find the nearest server?"},
            {"level": 2, "text": "DNS-based routing directs to nearest PoP. How do you decide what to cache and when to evict?"},
            {"level": 3, "text": "Multi-tier: Edge PoP → Regional → Origin Shield → Origin. LRU per tier. TTL for automatic expiry. Push invalidation for urgent updates via message bus."},
        ],
        "alternates": ["Design a video streaming service like Netflix."],
    },
    {
        "text": "Design a search autocomplete system that provides suggestions as the user types, handling millions of queries per second.",
        "difficulty": 3,
        "category": "system_design",
        "tags": ["data_structures", "scalability", "search"],
        "expected_time_min": 30,
        "optimal_approaches": [
            {
                "name": "Trie + Top-K Cache",
                "data_structures": ["trie", "cache"],
                "expected_complexity": {"time": "O(prefix_length)", "space": "O(queries * avg_length)"},
                "key_steps": ["Build trie from query corpus", "Store top-K results per node", "Periodic offline rebuild", "Browser-side caching + debounce"],
            },
        ],
        "common_pitfalls": ["Not discussing how to rank suggestions", "Missing personalization", "Not handling typos/fuzzy matching"],
        "hints": [
            {"level": 1, "text": "What data structure efficiently handles prefix lookups?"},
            {"level": 2, "text": "A Trie, but how do you return the top suggestions quickly? Pre-compute them at each node."},
            {"level": 3, "text": "Build trie offline from query logs with frequency. Store top-10 at each node. Serve from cache. Update trie periodically. Client-side debounce + cache."},
        ],
        "alternates": ["Design a spell-check system."],
    },
    {
        "text": "Design a ride-sharing matching system that connects riders with nearby drivers in real-time.",
        "difficulty": 3,
        "category": "system_design",
        "tags": ["geospatial", "real_time", "distributed_systems"],
        "expected_time_min": 30,
        "optimal_approaches": [
            {
                "name": "Geospatial Index + Matching Service",
                "data_structures": ["geohash", "priority_queue"],
                "expected_complexity": {"time": "O(log n) matching", "space": "O(drivers)"},
                "key_steps": ["GeoHash index for driver locations", "Match on proximity + ETA + rating", "Push notification to driver", "Handle acceptance/rejection/timeout"],
            },
        ],
        "common_pitfalls": ["Not handling driver location updates efficiently", "Missing surge pricing discussion", "Not addressing driver acceptance flow"],
        "hints": [
            {"level": 1, "text": "How do you efficiently find drivers near a given location?"},
            {"level": 2, "text": "GeoHash partitioning. But proximity alone isn't enough — what other factors matter for matching?"},
            {"level": 3, "text": "GeoHash for spatial indexing. Match on distance + ETA + driver rating. Send to best match first, cascade on timeout. Driver location updates via persistent connection."},
        ],
        "alternates": ["Design a food delivery system."],
    },
    {
        "text": "Design a distributed key-value store with configurable consistency levels (eventual, strong, quorum).",
        "difficulty": 5,
        "category": "system_design",
        "tags": ["distributed_systems", "architecture", "databases", "consistency"],
        "expected_time_min": 40,
        "optimal_approaches": [
            {
                "name": "Consistent Hashing + Quorum Replication",
                "data_structures": ["consistent_hash_ring", "vector_clocks"],
                "expected_complexity": {"time": "O(1) lookup + replication overhead", "space": "O(data * replication_factor)"},
                "key_steps": ["Consistent hashing for key distribution", "Sloppy quorum (W+R>N for strong)", "Vector clocks for conflict resolution", "Anti-entropy with Merkle trees"],
            },
        ],
        "common_pitfalls": ["Not discussing CAP trade-offs", "Missing failure detection", "Not handling network partitions"],
        "hints": [
            {"level": 1, "text": "Start with data partitioning. How do you distribute keys across nodes?"},
            {"level": 2, "text": "Consistent hashing. Now for consistency: what does quorum-based replication look like?"},
            {"level": 3, "text": "Ring with virtual nodes. For quorum: write to W nodes, read from R nodes, where W+R>N for strong consistency. Use vector clocks for conflict detection."},
        ],
        "alternates": ["Design a distributed cache like Memcached."],
    },
    {
        "text": "Design a real-time leaderboard system for an online game with millions of players.",
        "difficulty": 3,
        "category": "system_design",
        "tags": ["data_structures", "scalability", "real_time"],
        "expected_time_min": 25,
        "optimal_approaches": [
            {
                "name": "Redis Sorted Set + Partitioning",
                "data_structures": ["sorted_set", "cache"],
                "expected_complexity": {"time": "O(log n) update/rank", "space": "O(n)"},
                "key_steps": ["Redis ZADD/ZRANK for core operations", "Partition by region/game mode", "Periodic snapshots for history", "Pub/sub for real-time updates to UI"],
            },
        ],
        "common_pitfalls": ["Not handling ties", "Missing pagination for large leaderboards", "Not discussing historical leaderboards"],
        "hints": [
            {"level": 1, "text": "What data structure gives you O(log n) rank queries and updates?"},
            {"level": 2, "text": "Sorted set. Redis ZSET is perfect. But how do you handle millions of concurrent updates?"},
            {"level": 3, "text": "Redis ZADD for updates, ZREVRANGE for top-K, ZREVRANK for player rank. Partition by game/region. Cache top-100 at CDN edge."},
        ],
        "alternates": ["Design a tournament bracket system."],
    },
    # ═══ BEHAVIORAL ═════════════════════════════════════════════════════════
    {
        "text": "Tell me about a time you had to make a difficult technical decision with incomplete information. What was the outcome?",
        "difficulty": 2,
        "category": "behavioral",
        "tags": ["decision_making", "uncertainty", "leadership"],
        "expected_time_min": 8,
        "optimal_approaches": None,
        "common_pitfalls": ["Being vague about the technical details", "Not explaining the constraints and trade-offs", "Missing the outcome and learnings"],
        "hints": [
            {"level": 1, "text": "Ask them to be specific about what information was missing and why they couldn't wait to get it."},
            {"level": 2, "text": "Probe into the trade-offs they considered. Did they set up a reversible decision or commit fully?"},
            {"level": 3, "text": "Ask about the outcome, what they learned, and if they would decide differently today."},
        ],
        "alternates": ["Describe a time you chose the wrong technical approach. How did you recover?"],
    },
    {
        "text": "Describe a situation where you had to push back on a product requirement for technical reasons. How did you handle it?",
        "difficulty": 2,
        "category": "behavioral",
        "tags": ["communication", "stakeholder_management"],
        "expected_time_min": 8,
        "optimal_approaches": None,
        "common_pitfalls": ["Sounding adversarial rather than collaborative", "Not explaining the technical reasoning clearly", "Missing the resolution"],
        "hints": [
            {"level": 1, "text": "Look for evidence of empathy — did they understand why the PM wanted the feature?"},
            {"level": 2, "text": "Probe how they communicated the technical constraints. Did they offer alternatives?"},
            {"level": 3, "text": "Ask about the final outcome and the relationship with the PM afterward."},
        ],
        "alternates": ["Tell me about a time you influenced a technical decision without having formal authority."],
    },
    {
        "text": "Tell me about a time you mentored a junior engineer. What was your approach and what was the result?",
        "difficulty": 3,
        "category": "behavioral",
        "tags": ["leadership", "mentorship", "cross_team"],
        "expected_time_min": 8,
        "optimal_approaches": None,
        "common_pitfalls": ["Being too vague about their mentoring approach", "Not showing measurable growth in the mentee", "Taking all the credit"],
        "hints": [
            {"level": 1, "text": "Ask about specific activities — code reviews, pairing, 1:1s, project scoping?"},
            {"level": 2, "text": "Probe for how they adapted their approach based on the mentee's learning style."},
            {"level": 3, "text": "Ask for a concrete example of the mentee's growth — what could they do after that they couldn't before?"},
        ],
        "alternates": ["How have you helped improve your team's engineering practices?"],
    },
    {
        "text": "Describe a production incident you were involved in. How did you respond and what did you learn?",
        "difficulty": 2,
        "category": "behavioral",
        "tags": ["incident_response", "debugging", "communication"],
        "expected_time_min": 8,
        "optimal_approaches": None,
        "common_pitfalls": ["Not explaining the debugging process", "Missing the communication aspect", "Not discussing prevention measures"],
        "hints": [
            {"level": 1, "text": "Ask about how they triaged — how did they determine severity and scope?"},
            {"level": 2, "text": "Probe the debugging process. What tools did they use? How did they narrow down the root cause?"},
            {"level": 3, "text": "Ask about the post-mortem. What changes were made to prevent recurrence?"},
        ],
        "alternates": ["Tell me about a time a system you built failed unexpectedly. What happened?"],
    },
    {
        "text": "Tell me about a project where you had to work across multiple teams. What challenges did you face and how did you overcome them?",
        "difficulty": 3,
        "category": "behavioral",
        "tags": ["cross_team", "communication", "leadership"],
        "expected_time_min": 8,
        "optimal_approaches": None,
        "common_pitfalls": ["Not explaining the coordination challenges", "Missing specific examples of conflict resolution", "Being too abstract"],
        "hints": [
            {"level": 1, "text": "Ask about how they established alignment across teams with different priorities."},
            {"level": 2, "text": "Probe for specific conflicts or disagreements. How were they resolved?"},
            {"level": 3, "text": "Ask about communication mechanisms — documents, meetings, slack channels. What worked?"},
        ],
        "alternates": ["Describe a time when teams disagreed on a technical approach. How was it resolved?"],
    },
    {
        "text": "Describe a time you had to deliver a project under a tight deadline. What trade-offs did you make?",
        "difficulty": 2,
        "category": "behavioral",
        "tags": ["prioritization", "decision_making", "communication"],
        "expected_time_min": 8,
        "optimal_approaches": None,
        "common_pitfalls": ["Not explaining what was cut and why", "Missing stakeholder communication", "Not discussing technical debt created"],
        "hints": [
            {"level": 1, "text": "Ask about the prioritization framework they used."},
            {"level": 2, "text": "Probe the trade-offs: what was deferred? Was technical debt planned for cleanup?"},
            {"level": 3, "text": "Ask how they communicated the trade-offs to stakeholders and the cleanup plan."},
        ],
        "alternates": ["Tell me about a time you had to say no to a feature request."],
    },
    {
        "text": "Tell me about a technical strategy or vision you've driven at an organization level.",
        "difficulty": 4,
        "category": "behavioral",
        "tags": ["technical_strategy", "org_wide_impact", "technical_vision", "leadership"],
        "expected_time_min": 10,
        "optimal_approaches": None,
        "common_pitfalls": ["Not quantifying the impact", "Being too abstract about the strategy", "Not showing buy-in process"],
        "hints": [
            {"level": 1, "text": "Ask about the problem that motivated the strategy. What was the cost of not acting?"},
            {"level": 2, "text": "Probe how they built consensus across the organization. Who were the key stakeholders?"},
            {"level": 3, "text": "Ask about metrics: how did they measure success? What was the timeline and outcome?"},
        ],
        "alternates": ["Describe the most impactful architectural decision you've made."],
    },
    # ═══ MORE CODING (to reach 50+) ═════════════════════════════════════════
    {
        "text": "Implement a function to detect if a linked list has a cycle and find the start of the cycle.",
        "difficulty": 2,
        "category": "coding",
        "tags": ["linked_list", "two_pointers", "fundamentals"],
        "expected_time_min": 15,
        "optimal_approaches": [
            {
                "name": "Floyd's Tortoise and Hare",
                "data_structures": ["linked_list"],
                "expected_complexity": {"time": "O(n)", "space": "O(1)"},
                "key_steps": ["Slow and fast pointers", "If they meet, cycle exists", "Reset one to head, advance both by 1 to find start"],
            },
        ],
        "common_pitfalls": ["Not handling no-cycle case", "Off-by-one in cycle start detection"],
        "hints": [
            {"level": 1, "text": "Can you use two pointers moving at different speeds?"},
            {"level": 2, "text": "If slow moves 1 step and fast moves 2, they'll meet inside the cycle. Then what?"},
            {"level": 3, "text": "After meeting, reset slow to head. Move both by 1. They meet at the cycle start."},
        ],
        "alternates": ["Find the length of the cycle in a linked list."],
    },
    {
        "text": "Given a list of words, group all anagrams together.",
        "difficulty": 2,
        "category": "coding",
        "tags": ["strings", "hash_map", "sorting"],
        "expected_time_min": 15,
        "optimal_approaches": [
            {
                "name": "Sorted Key Grouping",
                "data_structures": ["hash_map"],
                "expected_complexity": {"time": "O(n * k log k)", "space": "O(n * k)"},
                "key_steps": ["Sort each word to create canonical key", "Group words by their sorted key in a hash map"],
            },
            {
                "name": "Character Count Key",
                "data_structures": ["hash_map"],
                "expected_complexity": {"time": "O(n * k)", "space": "O(n * k)"},
                "key_steps": ["Count characters of each word", "Use count tuple as key in hash map"],
            },
        ],
        "common_pitfalls": ["Not considering case sensitivity", "Using inefficient string comparison"],
        "hints": [
            {"level": 1, "text": "What property do all anagrams share?"},
            {"level": 2, "text": "Anagrams have the same characters sorted. Can you use that as a grouping key?"},
            {"level": 3, "text": "Create a hash map: key = sorted(word), value = list of words with that sorted form."},
        ],
        "alternates": ["Determine if two strings are anagrams."],
    },
    {
        "text": "Implement binary search on a rotated sorted array (e.g., [4,5,6,7,0,1,2]).",
        "difficulty": 2,
        "category": "coding",
        "tags": ["binary_search", "arrays", "algorithms"],
        "expected_time_min": 20,
        "optimal_approaches": [
            {
                "name": "Modified Binary Search",
                "data_structures": ["array"],
                "expected_complexity": {"time": "O(log n)", "space": "O(1)"},
                "key_steps": ["Find which half is sorted", "Check if target is in the sorted half", "Adjust search range accordingly"],
            },
        ],
        "common_pitfalls": ["Not handling duplicates", "Wrong comparison direction", "Off-by-one with mid calculation"],
        "hints": [
            {"level": 1, "text": "In a rotated sorted array, one half is always sorted. How can you leverage that?"},
            {"level": 2, "text": "At each step, determine which half is sorted by comparing nums[mid] with nums[left]."},
            {"level": 3, "text": "If left half sorted and target in range [left, mid), search left. Else search right. Vice versa for right half sorted."},
        ],
        "alternates": ["Find the minimum element in a rotated sorted array."],
    },
    {
        "text": "Given a list of intervals, merge all overlapping intervals.",
        "difficulty": 2,
        "category": "coding",
        "tags": ["sorting", "intervals", "arrays"],
        "expected_time_min": 15,
        "optimal_approaches": [
            {
                "name": "Sort and Merge",
                "data_structures": ["array"],
                "expected_complexity": {"time": "O(n log n)", "space": "O(n)"},
                "key_steps": ["Sort by start time", "Iterate, extending current interval or starting new one"],
            },
        ],
        "common_pitfalls": ["Not sorting first", "Missing edge case of one interval fully containing another"],
        "hints": [
            {"level": 1, "text": "What happens if you process intervals in order of start time?"},
            {"level": 2, "text": "Sort by start time. If current interval overlaps with previous, merge them."},
            {"level": 3, "text": "Sort by start. For each interval: if result is empty or last.end < curr.start, add new. Else last.end = max(last.end, curr.end)."},
        ],
        "alternates": ["Insert a new interval into a list of non-overlapping sorted intervals, merging if necessary."],
    },
    {
        "text": "Design and implement a thread-safe bounded blocking queue.",
        "difficulty": 3,
        "category": "coding",
        "tags": ["concurrency", "data_structures", "design_patterns"],
        "expected_time_min": 25,
        "optimal_approaches": [
            {
                "name": "Circular Buffer + Condition Variables",
                "data_structures": ["circular_buffer", "mutex", "condition_variable"],
                "expected_complexity": {"time": "O(1) per op", "space": "O(capacity)"},
                "key_steps": ["Circular buffer with head/tail", "Mutex for mutual exclusion", "Two condition variables: not_full and not_empty", "Put blocks when full, get blocks when empty"],
            },
        ],
        "common_pitfalls": ["Spurious wakeups (use while loop, not if)", "Deadlock from incorrect lock ordering", "Not handling interrupts"],
        "hints": [
            {"level": 1, "text": "You need mutual exclusion and a way to block/wake threads. What synchronization primitives do you need?"},
            {"level": 2, "text": "Use a mutex and condition variables. How many condition variables do you need?"},
            {"level": 3, "text": "Two conditions: not_full (for producers) and not_empty (for consumers). Use while-loop for condition checks to handle spurious wakeups."},
        ],
        "alternates": ["Implement a reader-writer lock."],
    },
    {
        "text": "Find the lowest common ancestor of two nodes in a binary tree.",
        "difficulty": 2,
        "category": "coding",
        "tags": ["trees", "recursion", "fundamentals"],
        "expected_time_min": 15,
        "optimal_approaches": [
            {
                "name": "Recursive DFS",
                "data_structures": ["binary_tree"],
                "expected_complexity": {"time": "O(n)", "space": "O(h)"},
                "key_steps": ["Base cases: null or found target", "Recurse left and right", "If both return non-null, current node is LCA"],
            },
        ],
        "common_pitfalls": ["Assuming BST when it's a general binary tree", "Not handling case where one node is ancestor of the other"],
        "hints": [
            {"level": 1, "text": "Start from the root. What information do you need from each subtree?"},
            {"level": 2, "text": "Recursively check if either target is in the left or right subtree. What does it mean if both subtrees contain a target?"},
            {"level": 3, "text": "If left returns non-null and right returns non-null, current node is LCA. If only one returns non-null, pass it up."},
        ],
        "alternates": ["Find the LCA in a BST (can you do it in O(h) time?)."],
    },
    {
        "text": "Implement a function that generates all valid combinations of n pairs of parentheses.",
        "difficulty": 2,
        "category": "coding",
        "tags": ["backtracking", "recursion", "algorithms"],
        "expected_time_min": 15,
        "optimal_approaches": [
            {
                "name": "Backtracking",
                "data_structures": ["string"],
                "expected_complexity": {"time": "O(4^n / sqrt(n))", "space": "O(n)"},
                "key_steps": ["Track open and close counts", "Add '(' if open < n", "Add ')' if close < open", "Base case: length == 2n"],
            },
        ],
        "common_pitfalls": ["Generating invalid sequences", "Not pruning when close > open"],
        "hints": [
            {"level": 1, "text": "This is a generation problem. When can you add '(' and when can you add ')'?"},
            {"level": 2, "text": "You can add '(' if open count < n. You can add ')' if close count < open count."},
            {"level": 3, "text": "Backtrack: start with empty string. Add '(' if open < n, add ')' if close < open. When length == 2n, add to result."},
        ],
        "alternates": ["Generate all valid IP addresses from a string of digits."],
    },
    {
        "text": "Design and implement a basic garbage collector using mark-and-sweep algorithm.",
        "difficulty": 5,
        "category": "coding",
        "tags": ["memory_management", "algorithms", "graphs"],
        "expected_time_min": 35,
        "optimal_approaches": [
            {
                "name": "Mark and Sweep",
                "data_structures": ["graph", "set"],
                "expected_complexity": {"time": "O(heap_size)", "space": "O(live_objects)"},
                "key_steps": ["Mark phase: traverse from roots via DFS/BFS", "Sweep phase: iterate heap, free unmarked objects", "Clear marks for next cycle"],
            },
        ],
        "common_pitfalls": ["Not handling cycles in object graph", "Missing root set", "Not clearing marks after sweep"],
        "hints": [
            {"level": 1, "text": "What are the two main phases? What do you need to track?"},
            {"level": 2, "text": "Mark: start from root set, DFS to mark all reachable objects. Sweep: free unmarked. What's the root set?"},
            {"level": 3, "text": "Roots = stack variables + globals. Mark phase: DFS from roots, set marked flag on each reachable object. Sweep: iterate all objects, free if not marked, clear mark if marked."},
        ],
        "alternates": ["Compare mark-and-sweep with reference counting. What are the trade-offs?"],
    },
    # ═══ MORE SYSTEM DESIGN ═════════════════════════════════════════════════
    {
        "text": "Design a web crawler that can crawl billions of pages efficiently. Cover politeness, deduplication, and prioritization.",
        "difficulty": 4,
        "category": "system_design",
        "tags": ["distributed_systems", "scalability", "architecture"],
        "expected_time_min": 35,
        "optimal_approaches": [
            {
                "name": "Distributed Crawler with URL Frontier",
                "data_structures": ["priority_queue", "bloom_filter", "hash_map"],
                "expected_complexity": {"time": "O(pages)", "space": "O(URLs seen)"},
                "key_steps": ["URL Frontier with priority and politeness queues", "Bloom filter for URL dedup", "DNS caching", "Distributed workers with domain partitioning"],
            },
        ],
        "common_pitfalls": ["Not respecting robots.txt", "Spider traps", "Not handling DNS resolution at scale"],
        "hints": [
            {"level": 1, "text": "Start with a single-threaded crawler. What are the bottlenecks when scaling?"},
            {"level": 2, "text": "URL frontier needs priority + politeness (per-domain rate limiting). How do you avoid re-crawling?"},
            {"level": 3, "text": "Bloom filter for seen URLs. Priority queue ordered by page importance. Per-host delay queue for politeness. Partition by domain hash across workers."},
        ],
        "alternates": ["Design a search engine indexing pipeline."],
    },
    {
        "text": "Design a payment processing system for an e-commerce platform. Cover consistency, idempotency, and failure handling.",
        "difficulty": 4,
        "category": "system_design",
        "tags": ["distributed_systems", "consistency", "architecture"],
        "expected_time_min": 35,
        "optimal_approaches": [
            {
                "name": "Saga Pattern with Idempotency",
                "data_structures": ["state_machine", "event_log"],
                "expected_complexity": {"time": "Depends on external APIs", "space": "O(transactions)"},
                "key_steps": ["Idempotency key per request", "Payment state machine", "Saga for multi-step (reserve → charge → confirm)", "Compensating transactions for rollback"],
            },
        ],
        "common_pitfalls": ["Not handling double-charge scenarios", "Missing reconciliation", "Not discussing PCI compliance"],
        "hints": [
            {"level": 1, "text": "What happens if the payment gateway times out? How do you prevent double charges?"},
            {"level": 2, "text": "Idempotency keys. Payment as a state machine. What are the states and transitions?"},
            {"level": 3, "text": "States: created → authorized → captured → settled (or failed/refunded). Idempotency key prevents replays. Saga pattern with compensating transactions for rollback."},
        ],
        "alternates": ["Design a digital wallet system."],
    },
    # ═══ MORE BEHAVIORAL ════════════════════════════════════════════════════
    {
        "text": "Tell me about a time you disagreed with a senior engineer or your manager about a technical approach. What happened?",
        "difficulty": 2,
        "category": "behavioral",
        "tags": ["communication", "decision_making", "stakeholder_management"],
        "expected_time_min": 8,
        "optimal_approaches": None,
        "common_pitfalls": ["Being negative about the other person", "Not showing respect for their perspective", "Not explaining the resolution"],
        "hints": [
            {"level": 1, "text": "Ask what specifically they disagreed about and why they felt strongly."},
            {"level": 2, "text": "Probe how they presented their case. Did they use data, prototypes, or other evidence?"},
            {"level": 3, "text": "Ask about the resolution and the relationship afterward. Did the experience change their approach to disagreements?"},
        ],
        "alternates": ["How do you handle code review feedback you disagree with?"],
    },
    {
        "text": "Describe a time you identified and addressed a significant technical debt in a system.",
        "difficulty": 3,
        "category": "behavioral",
        "tags": ["technical_strategy", "decision_making", "leadership"],
        "expected_time_min": 8,
        "optimal_approaches": None,
        "common_pitfalls": ["Not quantifying the impact of the tech debt", "Missing the business justification for addressing it", "Not showing the plan for migration"],
        "hints": [
            {"level": 1, "text": "Ask how they identified it was tech debt vs. acceptable complexity."},
            {"level": 2, "text": "Probe how they built the case for investing in the cleanup. What metrics did they use?"},
            {"level": 3, "text": "Ask about the migration strategy — did they do it incrementally or all at once? How did they manage risk?"},
        ],
        "alternates": ["How do you decide when to refactor versus when to accept technical debt?"],
    },
    {
        "text": "Tell me about a time you had to quickly learn a new technology or domain to deliver a project.",
        "difficulty": 2,
        "category": "behavioral",
        "tags": ["learning", "adaptability"],
        "expected_time_min": 8,
        "optimal_approaches": None,
        "common_pitfalls": ["Not explaining the learning approach", "Being vague about timelines", "Not connecting to delivery outcome"],
        "hints": [
            {"level": 1, "text": "Ask about their learning strategy — did they read docs, build prototypes, talk to experts?"},
            {"level": 2, "text": "Probe how they balanced learning with delivery pressure."},
            {"level": 3, "text": "Ask what they would do differently to ramp up faster if they faced a similar situation."},
        ],
        "alternates": ["How do you stay current with technology trends?"],
    },
    # ═══ ADDITIONAL CODING (pushing past 50) ═════════════════════════════════
    {
        "text": "Implement a word break algorithm: given a string and a dictionary, determine if the string can be segmented into dictionary words.",
        "difficulty": 3,
        "category": "coding",
        "tags": ["dynamic_programming", "strings", "algorithms"],
        "expected_time_min": 20,
        "optimal_approaches": [
            {
                "name": "Dynamic Programming",
                "data_structures": ["array", "hash_set"],
                "expected_complexity": {"time": "O(n^2)", "space": "O(n)"},
                "key_steps": ["dp[i] = true if s[0:i] can be segmented", "For each i, check all j < i: if dp[j] and s[j:i] in dict", "Return dp[n]"],
            },
        ],
        "common_pitfalls": ["Exponential backtracking without memoization", "Not handling empty string edge case"],
        "hints": [
            {"level": 1, "text": "Can you break this into subproblems? What does knowing 'the first k characters can be segmented' give you?"},
            {"level": 2, "text": "Define dp[i] = 'can s[0:i] be segmented?' How do you fill in dp[i] using previous values?"},
            {"level": 3, "text": "dp[0] = true. For each i from 1 to n: dp[i] = any(dp[j] and s[j:i] in dictionary) for j in 0..i-1."},
        ],
        "alternates": ["Return all possible word break segmentations."],
    },
    {
        "text": "Given a 2D board and a word, determine if the word exists in the grid. Letters must be adjacent (horizontally or vertically).",
        "difficulty": 2,
        "category": "coding",
        "tags": ["backtracking", "dfs", "graphs"],
        "expected_time_min": 20,
        "optimal_approaches": [
            {
                "name": "DFS Backtracking",
                "data_structures": ["matrix"],
                "expected_complexity": {"time": "O(m*n*4^L)", "space": "O(L)"},
                "key_steps": ["Try each cell as starting point", "DFS matching each character", "Mark visited, backtrack by unmarking"],
            },
        ],
        "common_pitfalls": ["Not unmarking cells after backtracking", "Not checking bounds", "Using BFS instead of DFS (won't work correctly)"],
        "hints": [
            {"level": 1, "text": "This is a search problem on a grid. What traversal strategy would work?"},
            {"level": 2, "text": "DFS from each cell matching the first character. Mark cells as visited, then backtrack."},
            {"level": 3, "text": "For each cell matching word[0], DFS with index. At each step, check bounds, visited, char match. Mark visited, recurse 4 directions, unmark."},
        ],
        "alternates": ["Find all words from a dictionary that exist in the grid (Word Search II — use Trie)."],
    },
    {
        "text": "Implement a function to find the kth smallest element in a BST.",
        "difficulty": 2,
        "category": "coding",
        "tags": ["trees", "bst", "algorithms"],
        "expected_time_min": 15,
        "optimal_approaches": [
            {
                "name": "Inorder Traversal",
                "data_structures": ["binary_tree", "stack"],
                "expected_complexity": {"time": "O(H + k)", "space": "O(H)"},
                "key_steps": ["Inorder traversal visits BST nodes in sorted order", "Count nodes visited", "Return when count reaches k"],
            },
        ],
        "common_pitfalls": ["Traversing the entire tree instead of stopping at k", "Not using BST property"],
        "hints": [
            {"level": 1, "text": "What traversal order gives you BST elements in sorted order?"},
            {"level": 2, "text": "Inorder traversal. Can you stop early once you've visited k nodes?"},
            {"level": 3, "text": "Iterative inorder with a stack. Push left chain. Pop, decrement k. If k == 0, return. Else go right."},
        ],
        "alternates": ["Find the kth largest element in a BST."],
    },
    {
        "text": "Design and implement a graph algorithm to detect a cycle in a directed graph.",
        "difficulty": 2,
        "category": "coding",
        "tags": ["graphs", "dfs", "algorithms"],
        "expected_time_min": 15,
        "optimal_approaches": [
            {
                "name": "DFS with Color Marking",
                "data_structures": ["graph", "array"],
                "expected_complexity": {"time": "O(V+E)", "space": "O(V)"},
                "key_steps": ["Mark nodes as white (unvisited), gray (in progress), black (done)", "Cycle exists if we visit a gray node"],
            },
        ],
        "common_pitfalls": ["Not distinguishing between back edges and cross edges", "Only checking visited without tracking recursion stack"],
        "hints": [
            {"level": 1, "text": "A cycle means revisiting a node. But you need to distinguish which kind of revisit indicates a cycle."},
            {"level": 2, "text": "In DFS, a back edge (to an ancestor in the current path) indicates a cycle. How do you track the current path?"},
            {"level": 3, "text": "Three states: white=unvisited, gray=in current DFS path, black=fully processed. Finding a gray node during DFS means cycle."},
        ],
        "alternates": ["Perform topological sort of a directed acyclic graph."],
    },
]


async def seed_questions() -> None:
    """Insert all seed questions into the database."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)

    # Create tables if they don't exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        for q_data in SEED_QUESTIONS:
            question = QuestionBank(
                text=q_data["text"],
                difficulty=q_data["difficulty"],
                category=q_data["category"],
                tags=q_data["tags"],
                expected_time_min=q_data["expected_time_min"],
                optimal_approaches=q_data.get("optimal_approaches"),
                common_pitfalls=q_data.get("common_pitfalls"),
                hints=q_data.get("hints"),
                alternates=q_data.get("alternates"),
            )
            session.add(question)

        await session.commit()
        print(f"Seeded {len(SEED_QUESTIONS)} questions into the question bank.")


if __name__ == "__main__":
    asyncio.run(seed_questions())
