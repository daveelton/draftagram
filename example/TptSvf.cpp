#include "TptSvf.h"

#include <algorithm>
#include <cmath>

void TptSvf::setSampleRate(double sampleRate)
{
    m_sampleRate = sampleRate;
    updateGain();
}

void TptSvf::setCutoff(double hertz)
{
    m_cutoff = std::clamp(hertz, 20.0, 0.45 * m_sampleRate);
    updateGain();
}

void TptSvf::setResonance(double amount)
{
    m_damping = 1.0 - std::clamp(amount, 0.0, 1.0);
}

void TptSvf::process(double input)
{
    const double g = m_gain;
    const double feedback = (2.0 * m_damping + g) * m_first.state() + m_second.state();

    m_highpass = (input - feedback) / (1.0 + 2.0 * m_damping * g + g * g);
    m_bandpass = m_first.process(g, m_highpass);
    m_lowpass = m_second.process(g, m_bandpass);
}

void TptSvf::reset()
{
    m_first.reset();
    m_second.reset();
    m_lowpass = m_bandpass = m_highpass = 0.0;
}

void TptSvf::updateGain()
{
    const double pi = 3.14159265358979323846;
    m_gain = std::tan(pi * m_cutoff / m_sampleRate);
}
