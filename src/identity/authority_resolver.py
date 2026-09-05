
"""
Authority Resolver Module
Determines the authoritative source system for a given asset based on configured rules.
"""

import logging
import yaml
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class AuthorityResolver:
    def __init__(self, config: Optional[Dict] = None, config_path: str = "config/source_authority.yaml"):
        """
        Initialize the AuthorityResolver.
        
        Args:
            config: Optional dictionary containing configuration.
            config_path: Path to the configuration file (default: config/source_authority.yaml).
        """
        self.rules = []
        is_loaded = False
        
        if config:
            parsed = self._parse_rules(config)
            if parsed:
                self.rules = parsed
                is_loaded = True
                
        if not is_loaded:
            # Fallback to file if config didn't have rules
            self.rules = self._load_rules_from_file(config_path)

    def _load_rules_from_file(self, path: str) -> List[Dict]:
        """Load rules from YAML file."""
        file_path = Path(path)
        if not file_path.is_absolute():
            # Assume relative to project root
            file_path = Path.cwd() / path
            
        if not file_path.exists():
            return []
            
        with open(file_path, 'r') as f:
            config = yaml.safe_load(f)
            return self._parse_rules(config)

    def _parse_rules(self, config: Dict) -> List[Dict]:
        """Parse and sort rules from config dictionary."""
        # Check 'rules' (new format) then 'defaults' (old format)
        rules = config.get('rules') or config.get('defaults') or []
        # Sort by priority (asc)
        return sorted(rules, key=lambda x: x.get('priority', 100))

    def _match_pattern(self, value: str, pattern: str) -> bool:
        """
        Match a value against a SQL-like pattern (supported: % wildcard).
        
        Args:
            value: The string to check (e.g. US_STK_AAPL)
            pattern: The pattern (e.g. US_STK_%)
        """
        import fnmatch
        
        # Convert SQL % to shell * (fnmatch support)
        # We don't support SQL _ (single char) unless we explicitly map it to ?
        # But for this requirement, % is the main one.
        fnmatch_pattern = pattern.replace('%', '*')
        
        return fnmatch.fnmatchcase(value, fnmatch_pattern)

    def _rule_authorities(self, rule: Dict) -> List[str]:
        """Return the authority list for a rule, tolerating both list and string forms.

        Supports:
          - ``authorities: [Schwab_CSV, Broker_IBKR]``  (new list form, C3b+)
          - ``authority: Schwab_CSV``                    (legacy string form)

        Returns an empty list if neither key is present.
        """
        if 'authorities' in rule:
            return list(rule['authorities'])
        if 'authority' in rule:
            return [rule['authority']]
        return []

    def coauthority_sources(self) -> "frozenset[str]":
        """Return the set of source systems that participate in any co-authority rule
        (a rule declaring >=2 authorities), e.g. frozenset({'Schwab_CSV', 'Broker_IBKR'}).
        Single-authority sources are excluded.
        """
        sources: set = set()
        for rule in self.rules:
            auths = self._rule_authorities(rule)
            if len(auths) >= 2:
                sources.update(auths)
        return frozenset(sources)

    def resolve(self, canonical_id: str, available_sources: Optional[List[str]] = None) -> Optional[str]:
        """
        Determine the authoritative source for a canonical ID.

        For co-authority rules (``authorities`` list) this deterministically returns
        the FIRST declared source that is available, e.g. Schwab_CSV.  For
        single-authority rules (``authority`` string) the output is byte-identical to
        the pre-C3b behaviour.

        Args:
            canonical_id: The asset's unique ID.
            available_sources: Optional list of sources that have this asset.
                               If provided, only returns a source if it exists in this list.

        Returns:
            The name of the authoritative source system or None if not found.
        """
        for rule in self.rules:
            pattern = rule.get('pattern')
            if not self._match_pattern(canonical_id, pattern):
                continue

            candidates = self._rule_authorities(rule)
            if available_sources is None:
                # Return the first declared authority (primary)
                if candidates:
                    return candidates[0]
            else:
                # Return the first declared authority that is actually available
                for candidate in candidates:
                    if candidate in available_sources:
                        return candidate
                # None of this rule's authorities are available — fall through to next rule

        # No rule yielded a usable authority.
        # See ADR-013 for priority semantics. This is rare in production because
        # config/source_authority.yaml carries a '*' catch-all; if it fires, it usually
        # means the catch-all authority is missing from available_sources for this asset.
        logger.debug(
            "authority_resolver: no rule matched canonical_id=%s "
            "(rules=%d, available_sources=%s)",
            canonical_id,
            len(self.rules),
            list(available_sources) if available_sources is not None else "any",
        )
        return None

    def resolve_authorities(
        self,
        canonical_id: str,
        available_sources: Optional[List[str]] = None,
    ) -> "frozenset[str]":
        """Return the full authority set for a canonical ID.

        For co-authority rules this may contain more than one source.  For
        single-authority rules the result is a 1-element frozenset.

        Args:
            canonical_id: The asset's unique ID.
            available_sources: Optional list of sources that have this asset.
                               If provided, filters the authority set to sources
                               present in ``available_sources``.  If the intersection
                               is empty the resolver falls through to the next matching
                               rule (mirrors the ``resolve()`` fall-through semantics).

        Returns:
            frozenset of authoritative source names, or ``frozenset()`` if none found.
        """
        for rule in self.rules:
            pattern = rule.get('pattern')
            if not self._match_pattern(canonical_id, pattern):
                continue

            rule_set = frozenset(self._rule_authorities(rule))
            if available_sources is None:
                return rule_set
            usable = rule_set & set(available_sources)
            if usable:
                return frozenset(usable)
            # No usable source in this rule — fall through to next matching rule

        logger.debug(
            "authority_resolver: resolve_authorities no rule matched canonical_id=%s "
            "(rules=%d, available_sources=%s)",
            canonical_id,
            len(self.rules),
            list(available_sources) if available_sources is not None else "any",
        )
        return frozenset()
