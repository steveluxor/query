-- KEYS[1]: rate limit key (e.g. "rate:42")
-- ARGV[1]: current timestamp in milliseconds
-- ARGV[2]: window size in seconds
-- ARGV[3]: max requests per window
-- ARGV[4]: unique member string to prevent ZSET collision

local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max = tonumber(ARGV[3])
local member = ARGV[4]
local cutoff = now - window * 1000

-- Remove entries outside the window
redis.call('ZREMRANGEBYSCORE', key, 0, cutoff)

-- Count remaining entries in the window
local count = redis.call('ZCARD', key)

if count >= max then
    return 0
end

-- Add current request and set TTL
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window + 60)
return 1
