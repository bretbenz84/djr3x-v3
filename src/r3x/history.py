"""Session-only source-preserving history, evicted as complete exchanges."""
from collections import deque
import json

from .contracts import ProviderFailure


class TurnHistory:
    max_turns = 32
    max_characters = 24_000

    def __init__(self):
        self.turns = deque()

    @staticmethod
    def size(messages):
        return len(json.dumps(messages,ensure_ascii=False,separators=(',',':')))

    def begin_turn(self):
        if not self.turns or self.turns[-1]:
            self.turns.append([])
        self._trim()

    def _trim(self):
        # October 11: evicting one exchange per turn once the budget was full changed
        # the start of every request, so hosted prompt caching stopped matching about
        # eight exchanges into a conversation. Evict down to two thirds of the budget
        # at once; the request prefix then holds until the budget fills again.
        if len(self.turns)<=self.max_turns and self.size(list(self))<=self.max_characters:
            return
        while len(self.turns)>1 and (len(self.turns)>self.max_turns*2//3 or
                                     self.size(list(self))>self.max_characters*2//3):
            self.turns.popleft()

    def append(self, message):
        if not self.turns:
            self.begin_turn()
        self.turns[-1].append(message)
        self._trim()

    def extend(self, messages):
        for message in messages:
            self.append(message)

    def clear(self):
        self.turns.clear()

    def __iter__(self):
        return (message for turn in self.turns for message in turn)

    def __len__(self):
        return sum(map(len,self.turns))

    def __getitem__(self, index):
        return list(self)[index]

    def __repr__(self):
        return repr(list(self))

    def messages(self):
        rows = list(self)
        # Fail explicitly if even the latest exchange cannot fit. Never silently
        # cut the current utterance or detach its evidence to meet the budget.
        if self.size(rows)>self.max_characters:
            raise ProviderFailure('ConversationTurnExceedsHistoryBudget')
        calls={r.get('call_id') for r in rows if r.get('type')=='function_call'}
        results={r.get('call_id') for r in rows if r.get('type')=='function_call_output'}
        return tuple(r for r in rows if r.get('type') not in {'function_call','function_call_output'}
                     or r.get('call_id') in calls & results)
