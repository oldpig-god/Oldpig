import unittest
import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf
from app.comovement import log_returns, residualize, block_ci


class ComovementTests(unittest.TestCase):
    def test_missing_session_does_not_bridge_or_fill(self):
        prices=pd.DataFrame({'a':[100.,np.nan,121.,133.1], 'b':[20.,22.,24.2,26.62]})
        r=log_returns(prices)
        self.assertTrue(r['a'].iloc[:3].isna().all())
        self.assertAlmostEqual(r['a'].iloc[3],np.log(1.1))
        self.assertEqual(len(r.dropna()),1)

    def test_price_scale_changes_covariance_but_not_returns(self):
        p=pd.DataFrame({'a':[10.,11.,10.,12.], 'b':[20.,19.,21.,22.]})
        scaled=p.copy();scaled['a']*=100
        np.testing.assert_allclose(log_returns(p).dropna(),log_returns(scaled).dropna(),atol=1e-14)
        self.assertAlmostEqual(scaled.cov().loc['a','b'],p.cov().loc['a','b']*100)

    def test_market_removal_recovers_independent_components(self):
        rng=np.random.default_rng(7)
        # Orthogonal components give a known residual answer, even under strong market loadings.
        q,_=np.linalg.qr(np.column_stack([np.ones(1000),rng.normal(size=(1000,3))]))
        m=pd.Series(q[:,1]);r=pd.DataFrame({'a':3*m+q[:,2], 'b':4*m+q[:,3]})
        residuals,beta=residualize(r,m)
        self.assertGreater(r.corr().iloc[0,1],.9)
        self.assertAlmostEqual(residuals.corr().iloc[0,1],0,places=12)
        np.testing.assert_allclose(beta[1],[3,4],atol=1e-12)
        shifted=r+pd.DataFrame({'a':m,'b':m})
        fixed,_=residualize(shifted,m,fit=beta)
        self.assertGreater(fixed.corr().iloc[0,1],.4)

    def test_shrinkage_on_collinear_assets_is_positive_definite(self):
        x=np.random.default_rng(9).normal(size=(600,2));x=np.column_stack([x,x[:,0]])
        self.assertLess(np.linalg.eigvalsh(np.cov(x.T)).min(),1e-12)
        self.assertGreater(np.linalg.eigvalsh(LedoitWolf().fit(x).covariance_).min(),0)

    def test_bootstrap_preserves_pairs_and_is_reproducible(self):
        x=np.random.default_rng(3).normal(size=250)
        np.testing.assert_allclose(block_ci(x,x),[1,1],atol=1e-12)
        self.assertEqual(block_ci(x,x[::-1]),block_ci(x,x[::-1]))

    def test_nonpositive_prices_rejected(self):
        with self.assertRaises(ValueError):log_returns(pd.DataFrame({'a':[1,0,2]}))

    def test_additive_dividend_adjustment_changes_return_denominator(self):
        raw=pd.DataFrame({'a':[26.20,24.13]})
        adjusted=raw-12.60
        self.assertGreater(abs(log_returns(adjusted).iloc[1,0]),abs(log_returns(raw).iloc[1,0]))


if __name__=='__main__':unittest.main()
