#include "TptIntegrator.h"

double TptIntegrator::process(double g, double input)
{
    const double v = g * input;
    const double output = v + m_state;
    m_state = output + v;
    return output;
}

void TptIntegrator::reset()
{
    m_state = 0.0;
}
