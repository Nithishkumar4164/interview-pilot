import { describe, it, expect } from 'vitest';
import { detectLanguage } from '../src/utils/language-detect';

describe('Language Detection', () => {
  it('detects Python', () => {
    const code = `
def two_sum(nums, target):
    seen = {}
    for i, num in enumerate(nums):
        if target - num in seen:
            return [seen[target - num], i]
        seen[num] = i
    `;
    expect(detectLanguage(code)).toBe('python');
  });

  it('detects JavaScript', () => {
    const code = `
function twoSum(nums, target) {
  const map = {};
  for (let i = 0; i < nums.length; i++) {
    const complement = target - nums[i];
    if (complement in map) {
      return [map[complement], i];
    }
    map[nums[i]] = i;
  }
}
    `;
    expect(detectLanguage(code)).toBe('javascript');
  });

  it('detects Java', () => {
    const code = `
public class Solution {
    public int[] twoSum(int[] nums, int target) {
        Map<Integer, Integer> map = new HashMap<>();
        for (int i = 0; i < nums.length; i++) {
            int complement = target - nums[i];
            if (map.containsKey(complement)) {
                return new int[]{map.get(complement), i};
            }
            map.put(nums[i], i);
        }
        return new int[]{};
    }
}
    `;
    expect(detectLanguage(code)).toBe('java');
  });

  it('detects Go', () => {
    const code = `
package main

import "fmt"

func twoSum(nums []int, target int) []int {
    seen := make(map[int]int)
    for i, num := range nums {
        if j, ok := seen[target-num]; ok {
            return []int{j, i}
        }
        seen[num] = i
    }
    return nil
}
    `;
    expect(detectLanguage(code)).toBe('go');
  });

  it('detects C++', () => {
    const code = `
#include <vector>
#include <unordered_map>
using namespace std;

vector<int> twoSum(vector<int>& nums, int target) {
    unordered_map<int, int> seen;
    for (int i = 0; i < nums.size(); i++) {
        int complement = target - nums[i];
        if (seen.count(complement)) {
            return {seen[complement], i};
        }
        seen[nums[i]] = i;
    }
    return {};
}
    `;
    expect(detectLanguage(code)).toBe('cpp');
  });

  it('detects TypeScript', () => {
    const code = `
interface Result {
  indices: number[];
}

function twoSum(nums: number[], target: number): number[] {
  const map: Record<number, number> = {};
  for (let i = 0; i < nums.length; i++) {
    const complement = target - nums[i];
    if (complement in map) {
      return [map[complement] as number, i];
    }
    map[nums[i]] = i;
  }
  return [];
}
    `;
    expect(detectLanguage(code)).toBe('typescript');
  });

  it('detects Rust', () => {
    const code = `
fn two_sum(nums: Vec<i32>, target: i32) -> Vec<i32> {
    let mut map = std::collections::HashMap::new();
    for (i, &num) in nums.iter().enumerate() {
        let complement = target - num;
        if let Some(&j) = map.get(&complement) {
            return vec![j as i32, i as i32];
        }
        map.insert(num, i);
    }
    vec![]
}
    `;
    expect(detectLanguage(code)).toBe('rust');
  });

  it('returns unknown for empty input', () => {
    expect(detectLanguage('')).toBe('unknown');
    expect(detectLanguage('   ')).toBe('unknown');
  });

  it('returns unknown for ambiguous code', () => {
    expect(detectLanguage('x = 1')).toBe('unknown');
  });
});
