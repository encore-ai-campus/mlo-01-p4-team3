"""Run the three shared Intent contract scenarios with the real implementation."""
from intent.parser import BaselineIntentParser
from tests.contract import test_fakes as contracts
from unittest.mock import patch


class RealIntentContract(contracts.Intent):
    """Keep the shared fake suite intact; substitute only its constructor here."""

    @classmethod
    def setUpClass(cls):
        """Build a real parser once for the inherited contract assertions."""
        cls.parser = BaselineIntentParser()

    def run(self, result=None):
        """Apply unchanged contract assertions to the real parser."""
        with patch("tests.contract.test_fakes.FakeIntentParser", return_value=self.parser):
            return super().run(result)
