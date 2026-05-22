# SSRF Analysis Guide

## Detection
- Check for user-controlled URLs in HTTP requests
- Look for requests made to internal IP ranges
- Identify URL fetching without validation

## Common Patterns
- requests.get(user_provided_url)
- urllib.request.urlopen(input_url)
- curl_exec(user_url)

## Recommendations
- Whitelist allowed domains/IPs
- Block requests to internal networks
- Use URL parsing to validate scheme and host
