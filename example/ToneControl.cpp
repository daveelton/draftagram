#include "ToneControl.h"

#include <algorithm>
#include <cmath>

void ToneControl::prepare(double sampleRate)
{
    m_filter.setSampleRate(sampleRate);
    m_filter.setResonance(0.3);
    m_filter.reset();
    setTone(m_tone);
}

void ToneControl::setTone(double amount)
{
    m_tone = std::clamp(amount, 0.0, 1.0);

    // 200 Hz at the darkest setting, 20 kHz at the brightest, in equal musical steps.
    m_filter.setCutoff(200.0 * std::pow(100.0, m_tone));
}

void ToneControl::process(double* samples, int numSamples)
{
    for (int i = 0; i < numSamples; ++i)
    {
        m_filter.process(samples[i]);
        samples[i] = m_filter.lowpass();
    }
}
